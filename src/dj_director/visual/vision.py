"""Vision service: frames from OBS -> fast eye (+ optional slow eye) -> director.

Implements the :class:`~dj_director.visual.interface.VisualAnalyzer` contract
used by the engine, plus ``issue`` (quality veto) and ``reveal_hint`` (slow
eye's pick for the drop). Owns its threads; every query is non-blocking.

* score     -1 when the shot is unusable (fast-eye problem or slow-eye verdict);
            otherwise a small bias: slow-eye interest and movement on the beat.
* activity  movement (in the region of interest when set) for novelty triggers.
* issue     "" or the reason the camera is vetoed.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from dj_director.config.schema import AppConfig, VisionConfig
from dj_director.visual.fast import CameraVisionStatus, FastVisualAnalyzer
from dj_director.visual.frames import FrameGrabber
from dj_director.visual.imaging import warm_up
from dj_director.visual.judge import JudgeResult, SceneJudge, Tile
from dj_director.visual.providers import ClaudeProvider, OllamaProvider, ProviderError

log = logging.getLogger(__name__)

INTEREST_GAIN = 0.6          # slow-eye interest 0..1 -> score bias of about -0.24..+0.36
COHERENCE_GAIN = 0.25        # movement pulsing with the beat


@dataclass(frozen=True)
class VisionStatus:
    enabled: bool
    frames_state: str          # off / connecting / waiting / running / auth_failed
    frames_error: str
    cameras: tuple             # CameraVisionStatus per camera
    judge_provider: str        # off / local / cloud
    judge_state: str
    judge_error: str
    judge: Optional[JudgeResult]


class VisionService:
    def __init__(self, cfg: AppConfig, password_provider: Callable[[], str],
                 api_key_provider: Callable[[], str], cameras: Callable[[], list[tuple[str, str]]],
                 context: Callable[[], dict], priority: Callable[[], Optional[str]] = lambda: None,
                 language: str = "en", clock: Callable[[], float] = time.monotonic) -> None:
        self.cfg = cfg
        self.password_provider = password_provider
        self.api_key_provider = api_key_provider
        self.cameras = cameras                  # () -> [(scene, role)]
        self.context = context
        self.priority = priority
        self.language = language
        self.clock = clock
        warm_up()
        self.fast = FastVisualAnalyzer()
        self.fast.set_rois(cfg.vision.rois)
        self.grabber: Optional[FrameGrabber] = None
        self.judge: Optional[SceneJudge] = None

    # ------------------------------------------------------------------ lifecycle
    @property
    def vcfg(self) -> VisionConfig:
        return self.cfg.vision

    def start(self) -> None:
        v = self.vcfg
        if v.enabled and self.grabber is None:
            o = self.cfg.obs
            self.grabber = FrameGrabber(o.host, o.port, self.password_provider,
                                        lambda: [n for n, _ in self.cameras()], self.fast.ingest,
                                        priority=self.priority, rate=v.rate)
            self.grabber.start()
        if v.enabled and v.judge_provider != "off" and self.judge is None:
            try:
                provider = (OllamaProvider(v.ollama_url, v.local_model) if v.judge_provider == "local"
                            else ClaudeProvider(self.api_key_provider(), v.cloud_model))
            except ProviderError as e:
                log.warning("slow eye not started: %s", e)
                self.judge = SceneJudge(None, self._tiles, self.context, v.judge_interval)
                self.judge.state, self.judge.error = "error", str(e)
                return
            self.judge = SceneJudge(provider, self._tiles, self.context, v.judge_interval, v.judge_timeout,
                                    self.language)
            self.judge.start()

    def stop(self) -> None:
        if self.grabber is not None:
            self.grabber.stop()
            self.grabber = None
        if self.judge is not None:
            self.judge.stop()
            self.judge = None

    def reconfigure(self, cfg: AppConfig) -> None:
        old = self.cfg
        self.cfg = cfg
        self.fast.set_rois(cfg.vision.rois)
        restart = (old.vision.enabled, old.vision.rate, old.vision.judge_provider, old.vision.local_model,
                   old.vision.ollama_url, old.vision.cloud_model, old.vision.judge_interval,
                   old.obs.host, old.obs.port) != (
            cfg.vision.enabled, cfg.vision.rate, cfg.vision.judge_provider, cfg.vision.local_model,
            cfg.vision.ollama_url, cfg.vision.cloud_model, cfg.vision.judge_interval, cfg.obs.host, cfg.obs.port)
        if restart:
            self.stop()
        self.start()                      # idempotent: starts only the parts not running

    def restart_judge(self) -> None:
        if self.judge is not None:
            self.judge.stop()
            self.judge = None
        self.start()

    def rename(self, old: str, new: str) -> None:
        self.fast.rename(old, new)

    def set_beat_period(self, period: float) -> None:
        self.fast.beat_period = period

    # ------------------------------------------------------------------ slow-eye inputs
    def _tiles(self) -> list[Tile]:
        now = self.clock()
        return [Tile(name, role, self.fast.latest_rgb(name, now)) for name, role in self.cameras()]

    def _judgement(self, name: str, now: float):
        r = self.judge.latest(now) if self.judge is not None else None
        return r.cameras.get(name) if r is not None else None

    # ------------------------------------------------------------------ VisualAnalyzer contract
    def score(self, camera_name: str) -> Optional[float]:
        now = self.clock()
        if self.issue(camera_name):
            return -1.0
        j = self._judgement(camera_name, now)
        coh = self.fast.coherence(camera_name, now)
        if j is None and coh <= 0:
            return None
        val = COHERENCE_GAIN * coh
        if j is not None:
            val += INTEREST_GAIN * (j.interest - 0.4)
        return val

    def available(self, camera_name: str) -> bool:
        return True

    def activity(self, camera_name: str) -> Optional[float]:
        return self.fast.activity(camera_name, self.clock())

    def issue(self, camera_name: str) -> str:
        now = self.clock()
        bad = self.fast.issue(camera_name, now)
        if bad:
            return bad
        j = self._judgement(camera_name, now)
        if j is not None and not j.usable:
            return f"AI: {j.issue or 'not usable'}"
        return ""

    def quality(self, camera_name: str) -> str:
        """HEALTHY / UNKNOWN / FAILED. No analysis (vision off, no picture yet) is UNKNOWN, never HEALTHY."""
        if not self.vcfg.enabled:
            return "UNKNOWN"
        if self.issue(camera_name):
            return "FAILED"
        return self.fast.quality(camera_name, self.clock())

    def reveal_hint(self) -> Optional[str]:
        r = self.judge.latest(self.clock()) if self.judge is not None else None
        return r.drop_pick if r is not None else None

    # ------------------------------------------------------------------ UI
    def status(self) -> VisionStatus:
        now = self.clock()
        cams = tuple(self.fast.status(n, now) for n, _ in self.cameras())
        g, j = self.grabber, self.judge
        return VisionStatus(self.vcfg.enabled, g.state if g else "off", g.error if g else "", cams,
                            self.vcfg.judge_provider, j.state if j else "off", j.error if j else "",
                            j.latest(now) if j else None)
