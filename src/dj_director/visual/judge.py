"""Slow eye: a vision-language model looks at all cameras every few seconds.

One numbered mosaic of every camera + the musical context goes to the model
(local Ollama or Claude API). It answers, per camera, whether the shot is
usable, how interesting and well framed it is *right now*, what moment it shows,
and - as an editor - whether to stay on the shot on air, which shot should come
next and which would best reveal the next drop. Since 0.5 the model also gets
the state of the edit (recent shots, time on air, framing of every camera) and
the fast eye's motion numbers, which a single still cannot show. Each verdict expires after ``ttl`` seconds:
the slow eye steers the director's choices, it never times a cut (that stays
with the beat-synchronous engine). Answers are validated and clamped; a
failing provider only backs off, it never stops the director.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

import numpy as np

from dj_director.visual.imaging import encode, mosaic
from dj_director.visual.providers import ProviderError

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the director of a live multicamera stream of a DJ set, with years of experience
cutting club nights and festival streams. You see one image with numbered tiles, one per camera,
taken right now, plus the state of the edit and of the music. Think like an editor:
- The wide master is home; inserts (hands on the mixer, side, crowd, behind the DJ) tell the moment.
- Cut to a shot only if it shows something the audience has not just seen: a clear change of
  scale, angle or subject. Never two near-identical shots in a row; avoid jumping front to back
  without a wide or overhead shot in between.
- Moments worth showing: the DJ's hands working the mixer or the jog wheels, an expressive gesture
  of the DJ, the crowd jumping or hands up, lights hitting the room on the beat.
- A shot that is soft, badly framed, half blocked or with the DJ cut out is weak even if usable.
- Motion numbers come from a fast detector: a still picture cannot show movement, trust them.
For every tile:
- usable: false only if black, blurred, blocked by someone in front of the lens, pointing at a wall
  or ceiling, or otherwise not airable. issue: empty when usable, else a few words.
- interest 0..1: how much there is to see right now.
- composition 0..1: framing quality (subject whole and readable, not cut, not obstructed).
- moment: what is happening in it: "none", "hands_on_mixer", "dj_gesture", "crowd_peak",
  "lights", "dj_with_crowd".
- what: what the tile shows, at most 8 words.
Then for the edit:
- hold: true if the shot on air shows a moment worth staying on a little longer.
- next_pick: the tile that should come next when the director cuts (a real change from what is on
  air and from the recent shots, worth showing now), or 0 if nothing is better than going home.
- drop_pick: the tile that would best reveal the next drop (wide, energy, crowd, lights), or 0.
- reason: one short sentence on why (next_pick or hold).
- note: one short sentence on the scene as a whole.
Be factual: describe only what is visible. Answer with the JSON object only."""

MOMENTS = ("none", "hands_on_mixer", "dj_gesture", "crowd_peak", "lights", "dj_with_crowd")


@dataclass(frozen=True)
class CameraJudgement:
    usable: bool
    interest: float
    issue: str
    what: str
    composition: float = 0.6
    moment: str = "none"


@dataclass(frozen=True)
class JudgeResult:
    t: float
    cameras: dict = field(default_factory=dict)        # scene -> CameraJudgement
    drop_pick: Optional[str] = None
    note: str = ""
    latency: float = 0.0
    model: str = ""
    next_pick: Optional[str] = None                    # the shot the AI would cut to next
    hold: Optional[str] = None                         # the on-air scene the AI wants to stay on
    reason: str = ""
    on_air: Optional[str] = None                       # what was on air when the AI looked


@dataclass(frozen=True)
class Tile:
    scene: str
    role: str
    rgb: Optional[np.ndarray]
    framing: str = ""                                  # "wide, DJ, front" from the camera metadata
    motion: Optional[float] = None                     # fast eye: movement (mixer region when set)
    beat_sync: float = 0.0                             # fast eye: movement / light pulsing on the beat


def _motion_words(t: Tile) -> str:
    if t.motion is None:
        return ""
    level = "strong" if t.motion >= 0.5 else "some" if t.motion >= 0.15 else "still"
    txt = f"motion {level} ({t.motion:.2f})"
    if t.beat_sync >= 0.4:
        txt += ", pulsing on the beat"
    return txt


def build_prompt(tiles: Sequence[Tile], context: dict, language: str) -> str:
    lines = ["Cameras:"]
    for i, t in enumerate(tiles, 1):
        bits = [t.role.lower()] + ([t.framing] if t.framing else [])
        extra = "no picture" if t.rgb is None else _motion_words(t)
        lines.append(f"{i}. {t.scene} ({'; '.join(bits)}){' - ' + extra if extra else ''}")
    lines.append("")
    music = f"Music now: {context.get('state', 'unknown')}, {context.get('bpm', 0):.0f} BPM"
    if context.get("beat_conf") is not None:
        music += f" (beat confidence {context['beat_conf']:.2f})"
    lines.append(music + ".")
    if context.get("gesture"):
        lines.append(f"The DJ is working the mixer: {context['gesture']}.")
    if context.get("since_drop") is not None:
        lines.append(f"Last drop {context['since_drop']:.0f} s ago.")
    if context.get("on_air"):
        on = f"On air: {context['on_air']}"
        if context.get("on_air_for") is not None:
            on += f" for {context['on_air_for']:.0f} s"
        if context.get("on_air_window"):
            on += f" (planned {context['on_air_window']})"
        lines.append(on + ".")
    if context.get("recent"):
        lines.append("Recent shots, oldest first: " + ", ".join(f"{n} {d:.0f}s" for n, d in context["recent"]) + ".")
    if context.get("next"):
        lines.append(f"Already planned: {context['next']}.")
    if context.get("coverage"):
        lines.append(f"Time share over the last minutes: {context['coverage']}.")
    lang = "Italian" if language == "it" else "English"
    lines.append(f"Write 'issue', 'what', 'reason' and 'note' in {lang}.")
    return "\n".join(lines)


def _unit(v, default: float) -> float:
    try:
        return min(1.0, max(0.0, float(v)))
    except (TypeError, ValueError):
        return default


def _tile_scene(n, tiles: Sequence[Tile]) -> Optional[str]:
    return tiles[n - 1].scene if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= len(tiles) else None


def parse_result(data: dict, tiles: Sequence[Tile], t: float, latency: float, model: str,
                 on_air: Optional[str] = None) -> JudgeResult:
    """Validate the model answer against the tiles that were actually sent."""
    if not isinstance(data, dict):
        raise ProviderError("unexpected answer")
    cams: dict[str, CameraJudgement] = {}
    for item in data.get("cameras", []) or []:
        if not isinstance(item, dict):
            continue
        n = item.get("n")
        if not isinstance(n, int) or not 1 <= n <= len(tiles) or tiles[n - 1].rgb is None:
            continue
        usable = bool(item.get("usable", True))
        moment = str(item.get("moment", "none"))
        cams[tiles[n - 1].scene] = CameraJudgement(
            usable, _unit(item.get("interest"), 0.0),
            str(item.get("issue", ""))[:80] if not usable else "", str(item.get("what", ""))[:80],
            _unit(item.get("composition"), 0.6), moment if moment in MOMENTS else "none")

    def ok(scene: Optional[str]) -> Optional[str]:
        # a pick is only kept for a camera that was seen and judged usable
        return scene if scene is not None and scene in cams and cams[scene].usable else None

    drop = ok(_tile_scene(data.get("drop_pick"), tiles))
    nxt = ok(_tile_scene(data.get("next_pick"), tiles))
    hold = on_air if (bool(data.get("hold", False)) and on_air in cams and cams[on_air].usable) else None
    if nxt is not None and nxt == on_air:
        hold, nxt = on_air, None                     # "the best shot is the one on air": stay on it
    return JudgeResult(t, cams, drop, str(data.get("note", ""))[:160], latency, model, nxt, hold,
                       str(data.get("reason", "")).replace(";", ",")[:160], on_air)


class SceneJudge:
    def __init__(self, provider, tiles: Callable[[], list[Tile]], context: Callable[[], dict],
                 interval: float = 8.0, timeout: float = 20.0, language: str = "en") -> None:
        self.provider = provider
        self.tiles = tiles
        self.context = context
        self.interval = interval
        self.timeout = timeout
        self.language = language
        self.ttl = max(15.0, 2.0 * interval)
        self.result: Optional[JudgeResult] = None
        self.state = "off"
        self.error = ""
        self.looks = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self.state = "starting"
        self._thread = threading.Thread(target=self._run, name="vision-judge", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)        # an HTTP call in flight finishes on its own
        self._thread = None
        self.state = "off"

    def latest(self, now: float) -> Optional[JudgeResult]:
        with self._lock:
            r = self.result
        return r if r is not None and now - r.t <= self.ttl else None

    def look_once(self) -> Optional[JudgeResult]:
        tiles = self.tiles()
        if not tiles or all(t.rgb is None for t in tiles):
            self.state = "waiting for camera pictures"
            return None
        image = encode(mosaic([t.rgb for t in tiles]), "JPG", 85)
        ctx = self.context()
        prompt = build_prompt(tiles, ctx, self.language)
        t0 = time.monotonic()
        data = self.provider.judge(image, SYSTEM_PROMPT, prompt, self.timeout)
        res = parse_result(data, tiles, time.monotonic(), time.monotonic() - t0,
                           getattr(self.provider, "model", ""), ctx.get("on_air"))
        with self._lock:
            self.result = res
        self.looks += 1
        self.state, self.error = "running", ""
        return res

    def _run(self) -> None:
        backoff = self.interval
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                self.look_once()
                backoff = self.interval
            except ProviderError as e:
                self.state, self.error = "error", str(e)
                log.warning("slow eye: %s", e)
                backoff = min(max(backoff * 2, self.interval), 60.0)
            except Exception as e:                     # never let the eye take the app down
                self.state, self.error = "error", str(e)
                log.exception("slow eye failed")
                backoff = 60.0
            self._stop.wait(max(0.5, backoff - (time.monotonic() - t0)))
