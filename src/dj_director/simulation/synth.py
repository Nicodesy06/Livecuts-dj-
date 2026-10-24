"""Deterministic synthetic DJ-style music generator with ground truth.

Used by tests, the editorial-quality harness and demos when no recorded set
is at hand. It is *not* meant to sound good; it reproduces the properties the
analysis relies on: four-on-the-floor kicks, off-beat hats, claps, a bass
line, a pad, breakdowns without kick/bass, build-ups and drops.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

ELEMENTS = ("kick", "hat", "clap", "bass", "pad", "riser")


@dataclass
class Section:
    name: str
    bars: int
    elements: tuple[str, ...]
    gain: float = 1.0


@dataclass
class SynthTrack:
    audio: np.ndarray            # mono float32
    sr: int
    bpm: float
    beat_times: np.ndarray
    downbeat_times: np.ndarray
    sections: list[tuple[str, float, float]] = field(default_factory=list)  # (name, start, end)


DJ_SET_SECTIONS = [
    Section("intro", 8, ("kick", "hat")),
    Section("groove", 16, ("kick", "hat", "clap", "bass")),
    Section("groove2", 16, ("kick", "hat", "clap", "bass", "pad")),
    Section("breakdown", 8, ("pad", "hat"), 0.8),
    Section("build", 8, ("pad", "hat", "riser", "clap"), 0.9),
    Section("drop", 16, ("kick", "hat", "clap", "bass", "pad")),
    Section("groove3", 16, ("kick", "hat", "clap", "bass")),
    Section("breakdown2", 8, ("pad",), 0.7),
    Section("drop2", 16, ("kick", "hat", "clap", "bass", "pad")),
    Section("outro", 8, ("kick", "hat")),
]


def _kick(sr: int) -> np.ndarray:
    n = int(0.25 * sr)
    t = np.arange(n) / sr
    f = 50 + 90 * np.exp(-t * 30)
    ph = 2 * np.pi * np.cumsum(f) / sr
    return (np.sin(ph) * np.exp(-t * 9)).astype(np.float32)


def _hat(sr: int, rng: np.random.Generator) -> np.ndarray:
    n = int(0.05 * sr)
    noise = rng.standard_normal(n)
    noise = np.diff(noise, prepend=0)          # crude high-pass
    return (noise * np.exp(-np.arange(n) / sr * 70) * 0.15).astype(np.float32)


def _clap(sr: int, rng: np.random.Generator) -> np.ndarray:
    n = int(0.15 * sr)
    noise = rng.standard_normal(n)
    env = np.exp(-np.arange(n) / sr * 25)
    return (noise * env * 0.25).astype(np.float32)


def generate(sections=None, bpm: float = 124.0, sr: int = 44100, seed: int = 1,
             timbre: float = 1.0, start_silence: float = 0.0) -> SynthTrack:
    sections = sections or DJ_SET_SECTIONS
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    total_beats = sum(s.bars * 4 for s in sections)
    off = int(start_silence * sr)
    n = off + int((total_beats * beat + 1.0) * sr)
    out = np.zeros(n, dtype=np.float32)
    kick, hat, clap = _kick(sr), _hat(sr, rng), _clap(sr, rng)

    def add(sig, pos, g=1.0):
        i = off + int(pos * sr)
        j = min(n, i + len(sig))
        if i < n:
            out[i:j] += sig[: j - i] * g

    beat_times, downbeats, secs = [], [], []
    b0 = 0
    for s in sections:
        s_start = b0 * beat
        for b in range(s.bars * 4):
            bt = (b0 + b) * beat
            beat_times.append(bt + start_silence)
            if b % 4 == 0:
                downbeats.append(bt + start_silence)
            g = s.gain
            if "kick" in s.elements:
                add(kick, bt, 0.9 * g)
            if "hat" in s.elements:
                add(hat, bt + beat / 2, g)
            if "clap" in s.elements and b % 2 == 1:
                add(clap, bt, g)
            if "bass" in s.elements:
                m = int(beat / 2 * sr)
                tt = np.arange(m) / sr
                f0 = 55.0 * timbre * (1.5 if (b // 4) % 2 else 1.0)
                saw = 2 * ((tt * f0) % 1.0) - 1
                env = np.minimum(1, tt * 200) * np.exp(-tt * 6)
                add((saw * env * 0.35).astype(np.float32), bt + beat / 2, g)
            if "riser" in s.elements:
                frac = b / (s.bars * 4)
                m = int(beat * sr)
                noise = rng.standard_normal(m).astype(np.float32)
                add(noise * 0.03 * (0.2 + frac) , bt, g)
                if frac > 0.5:
                    add(clap, bt + beat / 2, 0.5 * g * frac)
        if "pad" in s.elements:
            m = int(s.bars * 4 * beat * sr)
            tt = np.arange(m) / sr
            chord = sum(np.sin(2 * np.pi * f * timbre * tt) for f in (220.0, 277.2, 329.6))
            env = np.minimum(1, tt / 1.0) * np.minimum(1, (tt[::-1]) / 0.5)
            add((chord * env * 0.05 * s.gain).astype(np.float32), s_start)
        secs.append((s.name, s_start + start_silence, (b0 + s.bars * 4) * beat + start_silence))
        b0 += s.bars * 4
    peak = float(np.max(np.abs(out))) or 1.0
    out = (out / peak * 0.7).astype(np.float32)
    return SynthTrack(out, sr, bpm, np.array(beat_times), np.array(downbeats), secs)
