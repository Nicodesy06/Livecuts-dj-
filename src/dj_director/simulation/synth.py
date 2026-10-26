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


def track_sections(variant: int) -> list[Section]:
    """A few arrangement variants so a long mix does not repeat one structure."""
    base = [
        [Section("intro", 8, ("kick", "hat")), Section("groove", 16, ("kick", "hat", "clap", "bass")),
         Section("breakdown", 8, ("pad", "hat"), 0.8), Section("build", 8, ("pad", "hat", "riser", "clap"), 0.9),
         Section("drop", 16, ("kick", "hat", "clap", "bass", "pad")), Section("outro", 8, ("kick", "hat"))],
        [Section("intro", 16, ("kick",)), Section("groove", 24, ("kick", "hat", "bass")),
         Section("peak", 16, ("kick", "hat", "clap", "bass", "pad")), Section("breakdown", 8, ("pad",), 0.7),
         Section("drop", 16, ("kick", "hat", "clap", "bass")), Section("outro", 8, ("kick", "hat"))],
        [Section("intro", 8, ("kick", "hat")), Section("groove", 32, ("kick", "hat", "clap", "bass")),
         Section("breakdown", 16, ("pad", "hat"), 0.8), Section("drop", 24, ("kick", "hat", "clap", "bass", "pad")),
         Section("outro", 8, ("kick", "hat"))],
    ]
    return base[variant % len(base)]


def generate_mix(bpms=(122.0, 124.0, 124.0, 126.0, 128.0, 128.0), sr: int = 44100, seed: int = 3,
                 overlap_bars: int = 16) -> SynthTrack:
    """A long DJ-style session: several tracks blended with equal-power crossfades.

    Each incoming track starts on a bar line of the outgoing one and the mix
    point covers ``overlap_bars`` bars, like a DJ transition (tempi differ a little
    between tracks, as when the DJ nudges the tempo up during a set).
    """
    out = np.zeros(0, dtype=np.float32)
    beats, downbeats, secs = [], [], []
    t_off = 0.0
    for i, bpm in enumerate(bpms):
        tr = generate(track_sections(i), bpm=bpm, sr=sr, seed=seed + i, timbre=1.0 + 0.06 * (i % 4))
        a = tr.audio
        if len(out) == 0:
            out = a.copy()
            start = 0.0
        else:
            ov = int(overlap_bars * 4 * 60.0 / bpms[i - 1] * sr)
            ov = min(ov, len(out), len(a))
            start_i = len(out) - ov
            start = start_i / sr
            fade = np.linspace(0, np.pi / 2, ov, dtype=np.float32)
            mixed = out[start_i:] * np.cos(fade) + a[:ov] * np.sin(fade)
            out = np.concatenate([out[:start_i], mixed, a[ov:]])
            secs.append(("transition", start, start + ov / sr))
        beats.extend(b + start for b in tr.beat_times)
        downbeats.extend(d + start for d in tr.downbeat_times)
        secs.extend((f"t{i + 1}-{n}", a0 + start, b0 + start) for n, a0, b0 in tr.sections)
        t_off = start
    peak = float(np.max(np.abs(out))) or 1.0
    return SynthTrack((out / peak * 0.7).astype(np.float32), sr, float(bpms[0]),
                      np.array(sorted(beats)), np.array(sorted(downbeats)), secs)


# --------------------------------------------------------------------------- virtual DJ mixer
@dataclass(frozen=True)
class MixMove:
    """A DJ gesture on the mixer, applied to an already rendered track.

    kind:
      "low_kill"   isolator / EQ low kill: everything below ``freq`` removed (ramp ``ramp`` s)
      "high_kill"  isolator / EQ high kill: everything above ``freq`` removed
      "hp_sweep"   high-pass filter whose cutoff rises from ``freq`` to ``freq_end`` over the move
      "lp_sweep"   low-pass filter whose cutoff falls from ``freq`` to ``freq_end`` over the move
    """
    kind: str
    start: float
    end: float
    freq: float = 180.0
    freq_end: float = 1500.0
    ramp: float = 0.15


def apply_mixer(audio: np.ndarray, sr: int, moves: list[MixMove]) -> np.ndarray:
    """Apply mixer gestures with an STFT gain mask (sqrt-Hann, 75 % overlap, exact reconstruction)."""
    if not moves:
        return audio
    n_fft, hop = 2048, 512
    win = np.sqrt(np.hanning(n_fft + 1)[:-1]).astype(np.float64)
    pad = np.concatenate([np.zeros(n_fft), audio.astype(np.float64), np.zeros(n_fft)])
    out = np.zeros_like(pad)
    norm = np.zeros_like(pad)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    for i in range(0, len(pad) - n_fft, hop):
        t = (i + n_fft / 2 - n_fft) / sr                    # time of the frame centre in the track
        gain = np.ones_like(freqs)
        for m in moves:
            if t < m.start - m.ramp or t > m.end + m.ramp:
                continue
            depth = min(1.0, max(0.0, min(t - (m.start - m.ramp), (m.end + m.ramp) - t) / max(m.ramp, 1e-3)))
            frac = min(1.0, max(0.0, (t - m.start) / max(m.end - m.start, 1e-3)))
            if m.kind == "low_kill":
                cut = freqs < m.freq
            elif m.kind == "high_kill":
                cut = freqs > m.freq
            elif m.kind == "hp_sweep":
                fc = m.freq * (m.freq_end / m.freq) ** frac
                cut = freqs < fc
            elif m.kind == "lp_sweep":
                fc = m.freq * (m.freq_end / m.freq) ** frac
                cut = freqs > fc
            else:
                raise ValueError(m.kind)
            gain = np.where(cut, gain * (1.0 - depth * 0.997), gain)       # about -50 dB
        frame = pad[i:i + n_fft] * win
        spec = np.fft.rfft(frame) * gain
        out[i:i + n_fft] += np.fft.irfft(spec, n_fft) * win
        norm[i:i + n_fft] += win ** 2
    res = out / np.maximum(norm, 1e-9)
    return res[n_fft:n_fft + len(audio)].astype(np.float32)
