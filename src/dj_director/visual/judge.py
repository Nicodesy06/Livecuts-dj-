"""Slow eye: a vision-language model looks at all cameras every few seconds.

One numbered mosaic of every camera + the musical context goes to the model
(local Ollama or Claude API). It answers, per camera, whether the shot is
usable, how interesting it is *right now* and what it shows, plus which shot
would best reveal the next drop. Each verdict expires after ``ttl`` seconds:
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

SYSTEM_PROMPT = """You are the eye of a live multicamera director filming a DJ set for a stream.
You see one image with numbered tiles; each tile is one camera of the venue right now.
Judge every tile for the audience watching the stream:
- usable: false only if the shot is black, blurred, blocked by someone in front of the lens,
  badly framed (pointing at a wall or ceiling) or otherwise not airable.
- interest 0..1: how much there is to see right now. High: the DJ performing an expressive
  gesture, hands actively working the mixer, crowd jumping or hands up, lights hitting.
  Low: static, empty, nothing happening, a person just standing.
- issue: empty when usable, else a few words on the problem.
- what: what the tile shows, at most 8 words.
Then drop_pick: the tile number that would best reveal the next drop (wide, energy, crowd,
lights), or 0 if none suits. note: one short sentence on the scene as a whole.
Be factual: describe only what is visible. Answer with the JSON object only."""


@dataclass(frozen=True)
class CameraJudgement:
    usable: bool
    interest: float
    issue: str
    what: str


@dataclass(frozen=True)
class JudgeResult:
    t: float
    cameras: dict = field(default_factory=dict)        # scene -> CameraJudgement
    drop_pick: Optional[str] = None
    note: str = ""
    latency: float = 0.0
    model: str = ""


@dataclass(frozen=True)
class Tile:
    scene: str
    role: str
    rgb: Optional[np.ndarray]


def build_prompt(tiles: Sequence[Tile], context: dict, language: str) -> str:
    lines = ["Cameras:"]
    for i, t in enumerate(tiles, 1):
        lines.append(f"{i}. {t.scene} ({t.role.lower()}){'' if t.rgb is not None else ' - no picture'}")
    lines.append("")
    lines.append(f"Music now: {context.get('state', 'unknown')}, {context.get('bpm', 0):.0f} BPM.")
    if context.get("on_air"):
        lines.append(f"On air: {context['on_air']}.")
    if context.get("since_drop") is not None:
        lines.append(f"Last drop {context['since_drop']:.0f} s ago.")
    lang = "Italian" if language == "it" else "English"
    lines.append(f"Write 'issue', 'what' and 'note' in {lang}.")
    return "\n".join(lines)


def parse_result(data: dict, tiles: Sequence[Tile], t: float, latency: float, model: str) -> JudgeResult:
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
        try:
            interest = min(1.0, max(0.0, float(item.get("interest", 0.0))))
        except (TypeError, ValueError):
            interest = 0.0
        usable = bool(item.get("usable", True))
        cams[tiles[n - 1].scene] = CameraJudgement(usable, interest, str(item.get("issue", ""))[:80] if not usable
                                                   else "", str(item.get("what", ""))[:80])
    pick = data.get("drop_pick")
    drop = tiles[pick - 1].scene if isinstance(pick, int) and 1 <= pick <= len(tiles) else None
    if drop is not None and drop in cams and not cams[drop].usable:
        drop = None
    return JudgeResult(t, cams, drop, str(data.get("note", ""))[:160], latency, model)


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
        prompt = build_prompt(tiles, self.context(), self.language)
        t0 = time.monotonic()
        data = self.provider.judge(image, SYSTEM_PROMPT, prompt, self.timeout)
        res = parse_result(data, tiles, time.monotonic(), time.monotonic() - t0,
                           getattr(self.provider, "model", ""))
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
