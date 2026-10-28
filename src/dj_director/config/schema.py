"""Configuration schema with validation.

Configuration is stored as JSON. Loading is tolerant: unknown keys are
ignored, wrong types fall back to defaults and numeric values are clamped to
their documented ranges. Every correction is reported so the UI/log can show
it. Secrets (the OBS password) are never part of this schema; see
:mod:`dj_director.config.secrets`.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class CameraRole(str, Enum):
    MASTER = "MASTER"
    DETAIL = "DETAIL"
    SIDE = "SIDE"
    WIDE = "WIDE"
    OVERHEAD = "OVERHEAD"
    BEHIND = "BEHIND"
    CUSTOM = "CUSTOM"


# Editorial coverage classes used by the coverage budget (soft quota).
COVER_MASTER, COVER_DETAIL, COVER_SIDE = "master", "detail", "side"
ROLE_COVERAGE: dict[CameraRole, str] = {
    CameraRole.MASTER: COVER_MASTER,
    CameraRole.DETAIL: COVER_DETAIL,
    CameraRole.OVERHEAD: COVER_DETAIL,
    CameraRole.SIDE: COVER_SIDE,
    CameraRole.BEHIND: COVER_SIDE,
    CameraRole.WIDE: COVER_SIDE,
    CameraRole.CUSTOM: COVER_SIDE,
}

# Hard-cap ranges per coverage class (configurable within these bounds). Master: no rigid maximum.
HARD_CAP_RANGE = {COVER_DETAIL: (12.0, 15.0), COVER_SIDE: (20.0, 25.0)}


@dataclass(frozen=True)
class DurationSpec:
    """Shot-length grammar of a camera (seconds, before preset scaling).

    min        hard minimum before any cut away (except safety cases)
    pref_min / pref_max   preferred window: exits are planned inside it
    hard_cap   absolute maximum for secondary cameras (0 = none, i.e. master)
    cooldown   minimum time before the camera is used again (secondaries)
    """
    min: float
    pref_min: float
    pref_max: float
    hard_cap: float
    cooldown: float


# (min, preferred min, preferred max, hard cap [0 = class default / none], cooldown)
ROLE_DURATIONS: dict[CameraRole, tuple[float, float, float, float, float]] = {
    CameraRole.MASTER: (12.0, 20.0, 50.0, 0.0, 0.0),
    CameraRole.DETAIL: (5.0, 6.0, 10.0, 0.0, 20.0),
    CameraRole.OVERHEAD: (5.0, 6.0, 10.0, 0.0, 20.0),
    CameraRole.SIDE: (7.0, 10.0, 18.0, 0.0, 15.0),
    CameraRole.BEHIND: (7.0, 10.0, 18.0, 0.0, 15.0),
    CameraRole.WIDE: (7.0, 10.0, 18.0, 0.0, 15.0),
    CameraRole.CUSTOM: (7.0, 10.0, 18.0, 0.0, 15.0),
}

# Numeric ranges: field name -> (min, max). Used for clamping.
_RANGES: dict[str, tuple[float, float]] = {
    "port": (1, 65535),
    "connect_timeout": (0.5, 30),
    "request_timeout": (0.2, 30),
    "weight": (0.0, 3.0),
    "priority": (0, 10),
    # 0 (durations) / -1 (cooldown) mean "use the role default"
    "min_duration": (0.0, 600),
    "mean_duration": (0.0, 900),
    "max_duration": (0.0, 1800),
    "preferred_min": (0.0, 900),
    "preferred_max": (0.0, 900),
    "hard_cap": (0.0, 1800),
    "cooldown": (-1.0, 600),
    "sample_rate": (0, 384000),
    "block_size": (64, 8192),
    "silence_db": (-90, -20),
    "min_bpm": (40, 150),
    "max_bpm": (90, 260),
    "prior_bpm": (60, 200),
    "beats_per_bar": (2, 12),
    "sensitivity": (0.25, 3.0),
    "latency_offset_ms": (-500, 1000),
    "override_seconds": (1, 3600),
    "cut_threshold": (0.3, 3.0),
    "master_duration_scale": (0.2, 4.0),
    "insert_duration_scale": (0.2, 4.0),
    "duration_spread": (0.0, 1.0),
    "min_cut_gap": (0.5, 120),
    "max_cuts_per_minute": (0.5, 30),
    "max_consecutive_inserts": (1, 8),
    "music_trigger_gain": (0.0, 3.0),
    "musical_cut_confidence": (0.0, 1.0),
    "sync_confidence": (0.0, 1.0),
    "downbeat_confidence": (0.0, 1.0),
    "max_sync_wait": (0.0, 8.0),
    "hysteresis": (0.0, 2.0),
    "variety_jitter": (0.0, 1.0),
    "max_phrase_wait": (0.0, 30.0),
    "coverage_window": (60.0, 1800.0),
    "coverage_master": (0.0, 1.0),
    "coverage_detail": (0.0, 1.0),
    "coverage_side": (0.0, 1.0),
    "coverage_gain": (0.0, 3.0),
    "detail_hard_cap": HARD_CAP_RANGE[COVER_DETAIL],
    "side_hard_cap": HARD_CAP_RANGE[COVER_SIDE],
    "visual_novelty_threshold": (0.05, 1.0),
    "mix_shot_cap": (12.0, 25.0),
    "judge_interval": (3.0, 60.0),
    "judge_timeout": (2.0, 60.0),
}

VISION_RATES = ("low", "normal", "high")          # screenshot requests per second: 5 / 10 / 16
JUDGE_PROVIDERS = ("off", "local", "cloud")


@dataclass
class ObsConfig:
    host: str = "127.0.0.1"
    port: int = 4455
    auto_connect: bool = True
    connect_timeout: float = 5.0
    request_timeout: float = 3.0


@dataclass
class AudioConfig:
    backend: str = "input"            # "input" | "loopback" | "file"
    device_name: str = ""             # matched by name (indices change on hotplug)
    host_api: str = ""                # e.g. "Windows WASAPI"; empty = any
    channels: list[int] = field(default_factory=lambda: [0, 1])  # zero-based, mixed to mono
    sample_rate: int = 0              # 0 = device default
    block_size: int = 512
    file_path: str = ""
    file_loop: bool = True


@dataclass
class AnalysisConfig:
    silence_db: float = -55.0
    min_bpm: float = 70.0
    max_bpm: float = 180.0
    prior_bpm: float = 124.0
    beats_per_bar: int = 4
    sensitivity: float = 1.0          # >1 = reacts to smaller musical changes


@dataclass
class CameraConfig:
    scene_name: str = ""
    scene_uuid: str = ""
    role: CameraRole = CameraRole.CUSTOM
    enabled: bool = True
    is_master: bool = False
    fallback: bool = False
    weight: float = 1.0
    priority: int = 5
    min_duration: float = 0.0         # 0 = role default
    mean_duration: float = 0.0        # legacy (<= 0.1.2): mapped to a preferred window
    max_duration: float = 0.0         # legacy (<= 0.1.2): mapped to the hard cap
    cooldown: float = -1.0            # <0 = role default
    allowed_states: list[str] = field(default_factory=list)  # empty = all states
    preferred_min: float = 0.0        # 0 = role default
    preferred_max: float = 0.0
    hard_cap: float = 0.0             # 0 = class default (secondaries) / none (master)

    @property
    def coverage_class(self) -> str:
        return COVER_MASTER if self.is_master else ROLE_COVERAGE[self.role]

    def durations(self) -> DurationSpec:
        """Effective duration grammar, honouring legacy fields from older configs."""
        rmin, rpmin, rpmax, rcap, rcool = ROLE_DURATIONS[self.role]
        mn = self.min_duration or rmin
        if self.preferred_min or self.preferred_max:
            pmin = self.preferred_min or rpmin
            pmax = self.preferred_max or rpmax
        elif self.mean_duration:                     # 0.1.x config: a mean length
            pmin, pmax = self.mean_duration * 0.8, self.mean_duration * 1.2
        else:
            pmin, pmax = rpmin, rpmax
        pmin = max(pmin, mn)
        pmax = max(pmax, pmin)
        cap = self.hard_cap or self.max_duration or rcap      # legacy max -> hard cap
        cool = rcool if self.cooldown < 0 else self.cooldown
        return DurationSpec(mn, pmin, pmax, cap, cool)


@dataclass
class DirectorParams:
    """One engine, three parameter sets. See :mod:`dj_director.config.presets`."""
    cut_threshold: float = 1.0
    master_duration_scale: float = 1.0
    insert_duration_scale: float = 1.0
    duration_spread: float = 1.0      # share of the preferred window explored by the (deterministic)
                                      # variety sequence: 0 = always the centre, 1 = whole window
    min_cut_gap: float = 5.0
    max_cuts_per_minute: float = 5.0
    max_consecutive_inserts: int = 2
    music_trigger_gain: float = 1.0
    musical_cut_confidence: float = 0.55
    sync_confidence: float = 0.45
    prefer_downbeat: bool = True
    downbeat_confidence: float = 0.5
    max_sync_wait: float = 2.5
    hysteresis: float = 0.25          # resistance to chaining two secondaries without the master
    variety_jitter: float = 0.15      # deprecated (0.1.3+): the engine uses no randomness
    phrase_sync: bool = True          # prefer predicted phrase boundaries for non-urgent cuts
    max_phrase_wait: float = 9.0      # s a non-urgent cut may wait for the next phrase boundary
    # --- editorial coverage budget (soft quota over a sliding window)
    coverage_window: float = 300.0    # s
    coverage_master: float = 0.70
    coverage_detail: float = 0.15
    coverage_side: float = 0.15       # side / behind / wide / custom
    coverage_gain: float = 1.0        # how strongly the budget steers decisions (0 = ignore)
    editorial_cuts: bool = True       # keep musically-timed editorial inserts in stable music
    # --- hard caps of secondary shots (configurable inside 12-15 s / 20-25 s)
    detail_hard_cap: float = 14.0
    side_hard_cap: float = 22.0
    # --- visual opportunities: novelty of activity vs. its own baseline, never mere presence
    visual_novelty_threshold: float = 0.35
    # --- drop grammar (0.2): keep the reveal shot back during the build-up, open on it at the drop
    drop_reveal: bool = True
    # --- DJ gestures heard in the audio (0.3): EQ / isolator kills and filters -> mixer camera
    mix_gestures: bool = True
    mix_shot_cap: float = 14.0        # cap of a mixer shot held during a gesture (12-25 s; 14 = Detail max)


@dataclass
class DirectorConfig:
    preset: str = "natural"
    overrides: dict[str, Any] = field(default_factory=dict)  # DirectorParams field -> value
    override_policy: str = "timed"    # "timed" | "until_resume" | "adopt"
    override_seconds: float = 30.0
    no_signal_policy: str = "master"  # "master" | "hold"
    latency_offset_ms: float = 40.0   # command is sent this early w.r.t. the musical target
    seed: int = 0                     # 0 = random per session; fixed => reproducible
    drop_scene: str = ""              # scene opened on the drop ("" = automatic: wide / behind / side)
    mixer_scene: str = ""             # scene on the mixer for DJ gestures ("" = automatic: detail / overhead)


@dataclass
class VisionConfig:
    """Scene awareness (0.2). Frames come from OBS (GetSourceScreenshot, read-only)."""
    enabled: bool = True              # fast eye: quality veto, motion, mixer region
    rate: str = "normal"              # "low" | "normal" | "high" (OBS screenshot load)
    rois: dict[str, list[float]] = field(default_factory=dict)   # scene -> [x, y, w, h] in 0..1
    judge_provider: str = "off"       # slow eye: "off" | "local" (Ollama) | "cloud" (Claude API)
    local_model: str = "qwen3-vl:4b"
    ollama_url: str = "http://127.0.0.1:11434"
    cloud_model: str = "claude-opus-5-5"
    judge_interval: float = 8.0       # s between two looks of the slow eye
    judge_timeout: float = 20.0


@dataclass
class AppConfig:
    schema_version: int = 1
    first_run_done: bool = False
    shadow_mode: bool = False
    obs: ObsConfig = field(default_factory=ObsConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    director: DirectorConfig = field(default_factory=DirectorConfig)
    cameras: list[CameraConfig] = field(default_factory=list)
    vision: VisionConfig = field(default_factory=VisionConfig)
    log_decisions: bool = True
    ui_language: str = "auto"         # "auto" (system) | "it" | "en"

    def master_camera(self) -> Optional[CameraConfig]:
        for c in self.cameras:
            if c.is_master and c.enabled:
                return c
        return None


# --------------------------------------------------------------------------- (de)serialisation
def to_dict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, list):
        return [to_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj


def _coerce(name: str, default: Any, value: Any, issues: list[str], path: str) -> Any:
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        issues.append(f"{path}: expected bool")
        return default
    if isinstance(default, Enum):
        try:
            return type(default)(value)
        except ValueError:
            issues.append(f"{path}: invalid value {value!r}")
            return default
    if isinstance(default, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            issues.append(f"{path}: expected number")
            return default
        v = type(default)(value)
        if name in _RANGES:
            lo, hi = _RANGES[name]
            if v < lo or v > hi:
                issues.append(f"{path}: {v} clamped to [{lo}, {hi}]")
                v = type(default)(min(max(v, lo), hi))
        return v
    if isinstance(default, str):
        if isinstance(value, str):
            return value
        issues.append(f"{path}: expected string")
        return default
    return value


def from_dict(cls: type, data: Any, issues: Optional[list[str]] = None, path: str = "") -> Any:
    if issues is None:
        issues = []
    inst = cls()
    if not isinstance(data, dict):
        issues.append(f"{path or cls.__name__}: expected object")
        return inst
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        default = getattr(inst, f.name)
        p = f"{path}.{f.name}" if path else f.name
        if dataclasses.is_dataclass(default):
            setattr(inst, f.name, from_dict(type(default), value, issues, p))
        elif f.name == "cameras":
            if isinstance(value, list):
                setattr(inst, f.name, [from_dict(CameraConfig, v, issues, f"{p}[{i}]")
                                       for i, v in enumerate(value)])
            else:
                issues.append(f"{p}: expected list")
        elif f.name in ("channels",):
            if isinstance(value, list) and all(isinstance(x, int) and x >= 0 for x in value) and value:
                setattr(inst, f.name, list(value)[:8])
            else:
                issues.append(f"{p}: expected list of channel indices")
        elif f.name == "allowed_states":
            if isinstance(value, list) and all(isinstance(x, str) for x in value):
                setattr(inst, f.name, list(value))
            else:
                issues.append(f"{p}: expected list of strings")
        elif f.name == "rois":
            if isinstance(value, dict):
                setattr(inst, f.name, validate_rois(value, issues, p))
            else:
                issues.append(f"{p}: expected object")
        elif f.name == "overrides":
            if isinstance(value, dict):
                setattr(inst, f.name, validate_overrides(value, issues, p))
            else:
                issues.append(f"{p}: expected object")
        else:
            setattr(inst, f.name, _coerce(f.name, default, value, issues, p))
    _post_validate(inst, issues, path)
    return inst


def validate_rois(data: dict, issues: list[str], path: str = "rois") -> dict:
    """scene -> [x, y, w, h] normalised to the frame; clipped inside it, tiny regions dropped."""
    out: dict[str, list[float]] = {}
    for k, v in data.items():
        if not (isinstance(k, str) and isinstance(v, list) and len(v) == 4
                and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)):
            issues.append(f"{path}.{k}: expected [x, y, w, h]")
            continue
        x, y = min(max(float(v[0]), 0.0), 1.0), min(max(float(v[1]), 0.0), 1.0)
        w, h = min(max(float(v[2]), 0.0), 1.0 - x), min(max(float(v[3]), 0.0), 1.0 - y)
        if w < 0.05 or h < 0.05:
            issues.append(f"{path}.{k}: region too small, ignored")
            continue
        out[k] = [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]
    return out


def validate_overrides(data: dict, issues: list[str], path: str = "overrides") -> dict:
    defaults = DirectorParams()
    names = {f.name for f in dataclasses.fields(DirectorParams)}
    out = {}
    for k, v in data.items():
        if k not in names:
            issues.append(f"{path}.{k}: unknown parameter ignored")
            continue
        out[k] = _coerce(k, getattr(defaults, k), v, issues, f"{path}.{k}")
    return out


def _post_validate(inst: Any, issues: list[str], path: str) -> None:
    if isinstance(inst, AudioConfig) and inst.backend not in ("input", "loopback", "file"):
        issues.append(f"{path}.backend: unknown backend {inst.backend!r}")
        inst.backend = "input"
    if isinstance(inst, AnalysisConfig) and inst.min_bpm * 1.5 > inst.max_bpm:
        issues.append(f"{path}: BPM range too narrow, reset")
        inst.min_bpm, inst.max_bpm = 70.0, 180.0
    if isinstance(inst, DirectorConfig):
        if inst.override_policy not in ("timed", "until_resume", "adopt"):
            issues.append(f"{path}.override_policy: invalid, reset")
            inst.override_policy = "timed"
        if inst.no_signal_policy not in ("master", "hold"):
            issues.append(f"{path}.no_signal_policy: invalid, reset")
            inst.no_signal_policy = "master"
    if isinstance(inst, VisionConfig):
        if inst.rate not in VISION_RATES:
            issues.append(f"{path}.rate: invalid, reset")
            inst.rate = "normal"
        if inst.judge_provider not in JUDGE_PROVIDERS:
            issues.append(f"{path}.judge_provider: invalid, reset")
            inst.judge_provider = "off"
        if not inst.ollama_url.startswith(("http://", "https://")):
            issues.append(f"{path}.ollama_url: invalid, reset")
            inst.ollama_url = "http://127.0.0.1:11434"
    if isinstance(inst, AppConfig):
        if inst.ui_language not in ("auto", "it", "en"):
            issues.append("ui_language: invalid, reset")
            inst.ui_language = "auto"
        masters = [c for c in inst.cameras if c.is_master]
        if len(masters) > 1:
            issues.append("cameras: more than one master; keeping the first")
            for c in masters[1:]:
                c.is_master = False
        seen = set()
        unique = []
        for c in inst.cameras:
            if not c.scene_name or c.scene_name in seen:
                issues.append(f"cameras: duplicate/empty scene {c.scene_name!r} dropped")
                continue
            seen.add(c.scene_name)
            unique.append(c)
        inst.cameras = unique
