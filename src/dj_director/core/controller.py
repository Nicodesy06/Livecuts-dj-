"""Application controller: wires audio, analysis, director and OBS together.

Threads (all daemon, all with bounded work and timeouts):
* PortAudio / file callback  -> ring buffer          (audio.backends)
* ``audio-watchdog``         -> stall detection, reopen by device name
* ``analysis``               -> features, beats, music state (music.analyzer)
* ``director``               -> 20 Hz engine ticks; the ONLY thread mutating the engine
* ``obs-supervisor`` / ``obs-reader`` / ``obs-commands`` (obs.controller)
* GUI thread                 -> polls :meth:`snapshot` (never blocks on I/O)

Cross-thread requests to the engine (manual override, switch failures,
Force Master, rename) are queued and applied at the next director tick.

Director states:
OFF      not running.
ARMED    started, waiting for preconditions (OBS connected, a usable camera).
LIVE     deciding and commanding OBS.
SHADOW   deciding and logging only; OBS is never commanded.
PAUSED   started but holding; no decisions, no commands.
ERROR    unrecoverable configuration problem (e.g. no cameras); see message.
CALIBRATING  sync-offset calibration: two cameras alternate on the beat grid.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from dj_director.audio.manager import AudioInputManager
from dj_director.config.manager import ConfigManager
from dj_director.config.presets import resolve_params
from dj_director.config.schema import AppConfig
from dj_director.config.secrets import SecretStore
from dj_director.core.events import (AudioStatusEvent, DirectorAction, DirectorDecisionEvent,
                                     ExternalSceneChange, MusicSnapshot, ObsStatusEvent,
                                     SceneSwitchConfirmed, SceneSwitchFailed, SceneSwitchRequested)
from dj_director.diagnostics.logging_setup import DecisionLog, register_secret
from dj_director.director.calibration import CalibrationRunner
from dj_director.director.cameras import CameraRegistry
from dj_director.director.engine import DirectorEngine, EngineSettings
from dj_director.music.analyzer import AnalysisWorker
from dj_director.obs.controller import ObsController, SceneInfo
from dj_director.visual.vision import VisionService, VisionStatus

log = logging.getLogger(__name__)
TICK = 0.05
SECRET_KEY = "obs_password"
CLAUDE_KEY = "claude_api_key"


class DirectorStatus(str, Enum):
    OFF = "OFF"
    ARMED = "ARMED"
    LIVE = "LIVE"
    SHADOW = "SHADOW"
    PAUSED = "PAUSED"
    ERROR = "ERROR"
    CALIBRATING = "CALIBRATING"


@dataclass(frozen=True)
class LatencyReport:
    capture_ms: float
    analysis_ms: float
    dsp_cost_ms: float
    decision_ms: float
    command_rtt_ms: float
    offset_ms: float


@dataclass(frozen=True)
class AppSnapshot:
    director: DirectorStatus
    director_message: str
    shadow: bool
    current_camera: Optional[str]
    time_on_shot: float
    last_switch: Optional[DirectorDecisionEvent]
    last_decision: Optional[DirectorDecisionEvent]
    pending_target: Optional[str]
    audio: AudioStatusEvent
    music: Optional[MusicSnapshot]
    obs: ObsStatusEvent
    latency: LatencyReport
    preset: str
    cuts_last_minute: int
    override_active: bool
    coverage: tuple = ()               # ((class, share, target), ...) over the sliding window
    vision: Optional[VisionStatus] = None
    reveal_camera: Optional[str] = None


class AppController:
    def __init__(self, config_manager: Optional[ConfigManager] = None,
                 secrets: Optional[SecretStore] = None) -> None:
        self.config_manager = config_manager or ConfigManager()
        self.secrets = secrets or SecretStore(self.config_manager.path.parent)
        self.config: AppConfig = self.config_manager.load()
        register_secret(self.secrets.get(SECRET_KEY))
        register_secret(self.secrets.get(CLAUDE_KEY))
        self.audio = AudioInputManager(self.config.audio, self.config.analysis.silence_db)
        self.analysis = AnalysisWorker(self.audio, self.config.analysis)
        self.obs = ObsController(self.config.obs, lambda: self.secrets.get(SECRET_KEY))
        self.vision = VisionService(
            self.config, lambda: self.secrets.get(SECRET_KEY), lambda: self.secrets.get(CLAUDE_KEY),
            cameras=lambda: [(c.scene_name, c.role.value) for c in list(self.config.cameras) if c.enabled],
            context=self._vision_context, priority=self._reveal_name,
            language=self.config.ui_language if self.config.ui_language in ("it", "en") else "en")
        self.visual = self.vision
        self.decision_log = DecisionLog(enabled=self.config.log_decisions)
        self.decisions: deque[DirectorDecisionEvent] = deque(maxlen=300)
        self.status = DirectorStatus.OFF
        self.message = ""
        self.shadow = self.config.shadow_mode
        self._commands: "queue.Queue[tuple]" = queue.Queue(maxsize=256)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._last_switch: Optional[DirectorDecisionEvent] = None
        self._last_hold_reason = ""
        self._last_hold_logged = 0.0
        self._inflight: dict[int, tuple[str, Optional[str]]] = {}   # cid -> (scene, previous)
        self._decision_ms = 0.0
        self.calibration: Optional[CalibrationRunner] = None
        self.registry = CameraRegistry.from_configs(self.config.cameras)
        self.engine = self._build_engine()
        self.obs.on_external_change = lambda e: self._post("external", e)
        self.obs.on_switch_result = lambda e: self._post("switch_result", e)
        self.obs.on_scene_renamed = lambda old, new: self._post("rename", old, new)
        self.obs.on_scenes_changed = lambda scenes: self._post("scenes", scenes)

    # ------------------------------------------------------------------ setup
    def _build_engine(self) -> DirectorEngine:
        d = self.config.director
        settings = EngineSettings(beats_per_bar=self.config.analysis.beats_per_bar,
                                  latency_offset=d.latency_offset_ms / 1000.0,
                                  override_policy=d.override_policy,
                                  override_seconds=d.override_seconds,
                                  no_signal_policy=d.no_signal_policy,
                                  drop_scene=d.drop_scene, mixer_scene=d.mixer_scene)
        seed = d.seed or int(time.time())
        return DirectorEngine(self.registry, resolve_params(d), settings, seed=seed,
                              visual_fit=lambda cam: self.visual.score(cam.name) or 0.0,
                              visual_activity=lambda cam: self.visual.activity(cam.name),
                              visual_issue=lambda cam: self.visual.issue(cam.name),
                              reveal_hint=self.visual.reveal_hint)

    def _vision_context(self) -> dict:
        snap = self.analysis.latest()
        e = self.engine
        now = time.monotonic()
        return {"state": snap.state.label if snap else "unknown",
                "bpm": snap.beat.bpm if snap else 0.0,
                "on_air": e.current,
                "since_drop": now - e.phrase_anchor if e.phrase_anchor else None}

    def _reveal_name(self) -> Optional[str]:
        try:
            rc = self.engine.reveal_camera()
        except Exception:
            return None
        return rc.name if rc is not None else None

    def start_services(self) -> None:
        """Start audio capture/analysis and OBS connection (director stays OFF)."""
        self.audio.start()
        self.analysis.start()
        if self.config.obs.auto_connect:
            self.obs.start()
            self.vision.start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="director", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self.stop_director()
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.vision.stop()
        self.analysis.stop()
        self.audio.stop()
        self.obs.stop()
        self.decision_log.close()

    def save_config(self) -> None:
        self.config_manager.save(self.config)

    def set_password(self, password: str) -> None:
        self.secrets.set(SECRET_KEY, password)
        register_secret(password)

    def get_password(self) -> str:
        return self.secrets.get(SECRET_KEY)

    def set_claude_key(self, key: str) -> None:
        self.secrets.set(CLAUDE_KEY, key)
        register_secret(key)
        if self.config.vision.judge_provider == "cloud":
            self.vision.restart_judge()

    def has_claude_key(self) -> bool:
        return bool(self.secrets.get(CLAUDE_KEY))

    def apply_config(self, cfg: AppConfig, restart_audio: bool = False, reconnect_obs: bool = False) -> None:
        """Apply a new configuration from the UI (thread-safe)."""
        with self._lock:
            self.config = cfg
            self.shadow = cfg.shadow_mode
            self.decision_log.enabled = cfg.log_decisions
        self.save_config()
        self._post("rebuild")
        if cfg.obs.auto_connect:
            self.vision.reconfigure(cfg)
        else:
            self.vision.stop()
        if restart_audio:
            self.audio.silence_db = cfg.analysis.silence_db
            self.analysis.cfg = cfg.analysis
            self.analysis.pipeline = None
            self.audio.reconfigure(cfg.audio)
        if reconnect_obs:
            if cfg.obs.auto_connect:
                self.obs.cfg = cfg.obs
                self.obs.start()
                self.obs.reconnect_now()
            else:
                self.obs.stop()

    # ------------------------------------------------------------------ user commands
    def start_director(self, shadow: Optional[bool] = None) -> None:
        if shadow is not None:
            self.shadow = shadow
        self._post("start")

    def stop_director(self) -> None:
        self._post("stop")

    def pause(self) -> None:
        self._post("pause")

    def resume(self) -> None:
        self._post("resume")

    def force_master(self) -> None:
        self._post("force_master")

    def start_calibration(self, cam_a: str, cam_b: str, every_beats: int = 8) -> None:
        self._post("calibrate", CalibrationRunner(cam_a, cam_b, every_beats))

    def set_latency_offset(self, ms: float) -> None:
        """Live offset change (calibration slider); persisted, engine rebuilt."""
        self.config.director.latency_offset_ms = float(ms)
        self.engine.s.latency_offset = ms / 1000.0

    def _post(self, *cmd) -> None:
        try:
            self._commands.put_nowait(cmd)
        except queue.Full:
            log.error("director command queue full; dropping %s", cmd[0])

    # ------------------------------------------------------------------ director thread
    def _run(self) -> None:
        next_t = time.monotonic()
        while not self._stop.is_set():
            try:
                self._drain_commands()
                self._tick()
            except Exception:
                log.exception("director tick failed")
                self._set_status(DirectorStatus.ERROR, "internal error; see log")
            next_t += TICK
            delay = next_t - time.monotonic()
            if delay < -1.0:
                next_t = time.monotonic()      # fell far behind: resync, don't burst
            self._stop.wait(max(0.0, delay))

    def _set_status(self, s: DirectorStatus, msg: str = "") -> None:
        with self._lock:
            if s != self.status or msg != self.message:
                log.info("director %s %s", s.value, msg)
            self.status, self.message = s, msg

    def _drain_commands(self) -> None:
        now = time.monotonic()
        while True:
            try:
                cmd = self._commands.get_nowait()
            except queue.Empty:
                return
            kind = cmd[0]
            if kind == "start":
                self.engine = self._build_engine()
                if self.obs.connected and self.registry.get(self.obs.program):
                    # start from what is on air if it is one of our cameras; otherwise the
                    # engine's first decision is an immediate cut to the master
                    self.engine.sync_current(self.obs.program, now)
                self._set_status(DirectorStatus.ARMED, "starting")
            elif kind == "stop":
                self.engine.pending = None
                if self.calibration is not None:
                    self.calibration = None
                    self.save_config()            # keep the calibrated offset
                self._set_status(DirectorStatus.OFF, "")
            elif kind == "calibrate":
                self.engine.pending = None
                self.calibration = cmd[1]
                self._set_status(DirectorStatus.CALIBRATING, self.calibration.message)
            elif kind == "pause":
                if self.status != DirectorStatus.OFF:
                    self.engine.pending = None
                    self._set_status(DirectorStatus.PAUSED, "paused by user")
            elif kind == "resume":
                if self.status in (DirectorStatus.PAUSED, DirectorStatus.ARMED, DirectorStatus.LIVE,
                                   DirectorStatus.SHADOW):
                    self.engine.clear_override()
                    if self.obs.connected and not self.shadow:
                        self.engine.sync_current(self.obs.program or None, now)
                    self._set_status(DirectorStatus.ARMED, "resuming")
            elif kind == "force_master":
                anchor = self.registry.anchor()
                if anchor is not None and self.status != DirectorStatus.OFF:
                    prev = self.engine.current
                    d = self.engine.force(anchor.name, now, "Force Master (operator)")
                    self._record(d)
                    self._execute(d, prev)
                elif anchor is not None and self.obs.can_switch():
                    # director off: still honour the explicit operator command
                    self.obs.switch_program(anchor.name, 0)
            elif kind == "external":
                e: ExternalSceneChange = cmd[1]
                if self.status in (DirectorStatus.LIVE, DirectorStatus.ARMED, DirectorStatus.PAUSED):
                    if e.scene != self.engine.current:
                        d = self.engine.on_external_change(e.scene, now)
                        self._record(d)
            elif kind == "switch_result":
                ev = cmd[1]
                self.decision_log.write(type(ev).__name__, ev)
                info = self._inflight.pop(ev.correlation_id, None)
                if isinstance(ev, SceneSwitchFailed) and info is not None:
                    scene, previous = info
                    if self.engine.current == scene:
                        self.engine.on_switch_failed(scene, previous, now)
            elif kind == "rename":
                old, new = cmd[1], cmd[2]
                if self.registry.rename(old, new):
                    for c in self.config.cameras:
                        if c.scene_name == old:
                            c.scene_name = new
                    if self.engine.current == old:
                        self.engine.current = new
                    if old in self.engine._novelty:
                        self.engine._novelty[new] = self.engine._novelty.pop(old)
                    if old in self.config.vision.rois:
                        self.config.vision.rois[new] = self.config.vision.rois.pop(old)
                    if self.config.director.drop_scene == old:
                        self.config.director.drop_scene = new
                        self.engine.s.drop_scene = new
                    if self.config.director.mixer_scene == old:
                        self.config.director.mixer_scene = new
                        self.engine.s.mixer_scene = new
                    self.vision.rename(old, new)
                    for s in self.engine.history:
                        if s.camera == old:
                            s.camera = new
                    self.save_config()
                    log.info("camera scene renamed in OBS: %s -> %s (config updated)", old, new)
            elif kind == "scenes":
                scenes: list[SceneInfo] = cmd[1]
                missing = self.registry.set_available(s.name for s in scenes)
                for m in missing:
                    log.warning("camera scene '%s' no longer exists in OBS", m)
            elif kind == "rebuild":
                # settings changed mid-session: keep the editorial state so the new
                # parameters cannot cause a burst of cuts (history, cut times, override)
                old = self.engine
                self.registry = CameraRegistry.from_configs(self.config.cameras)
                if self.obs.scenes_valid:
                    self.registry.set_available(s.name for s in self.obs.scenes)
                self.engine = self._build_engine()
                for attr in ("history", "cut_times", "current", "shot_start", "shot_target",
                             "override_until", "consecutive_inserts", "_variety_index", "_novelty",
                             "phrase_anchor", "_anchor_period", "_mix_shot", "_gesture_off_since", "_last_frac"):
                    setattr(self.engine, attr, getattr(old, attr))
                cam = self.registry.get(self.engine.current)
                self.engine.shot_spec = self.engine.spec(cam) if cam is not None else None
                if self.engine._mix_shot and cam is not None:
                    self.engine._open_mix_shot()      # a mixer shot keeps its gesture window
                    self.engine.shot_target = old.shot_target

    def _tick(self) -> None:
        st = self.status
        if st == DirectorStatus.CALIBRATING:
            self._tick_calibration()
            return
        if st in (DirectorStatus.OFF, DirectorStatus.PAUSED, DirectorStatus.ERROR):
            self.analysis.drain_events()       # don't let stale events trigger cuts on resume
            return
        now = time.monotonic()
        if not len(self.registry.usable()) and not self.shadow:
            self._set_status(DirectorStatus.ARMED, "waiting: no usable camera scene in OBS")
            return
        if len(self.registry) == 0:
            self._set_status(DirectorStatus.ERROR, "no cameras configured")
            return
        if not self.shadow:
            if not self.obs.can_switch():
                if st == DirectorStatus.LIVE:
                    self.engine.pending = None
                self._set_status(DirectorStatus.ARMED, "waiting for OBS connection")
                return
            if st != DirectorStatus.LIVE:
                # (re)entering LIVE: adopt what is on air if it is one of our cameras
                if self.registry.get(self.obs.program) and self.engine.current != self.obs.program:
                    self.engine.sync_current(self.obs.program, now)
                self._set_status(DirectorStatus.LIVE, "")
        elif st != DirectorStatus.SHADOW:
            for c in self.registry:
                if not self.obs.scenes_valid:
                    c.available = True           # shadow without OBS: assume all present
            self._set_status(DirectorStatus.SHADOW, "decisions are logged, OBS is not commanded")
        snap = self.analysis.latest()
        events = self.analysis.drain_events()
        self.vision.set_beat_period(snap.beat.period if snap is not None else 0.0)
        t0 = time.perf_counter()
        prev = self.engine.current
        d = self.engine.tick(now, snap, events)
        self._decision_ms = 0.9 * self._decision_ms + 0.1 * (time.perf_counter() - t0) * 1000
        self._record(d)
        if d.action == DirectorAction.SWITCH:
            self._execute(d, prev)

    def _tick_calibration(self) -> None:
        cal = self.calibration
        if cal is None:
            self._set_status(DirectorStatus.OFF, "")
            return
        self.analysis.drain_events()
        if not self.obs.can_switch():
            self._set_status(DirectorStatus.CALIBRATING, "waiting for OBS connection")
            return
        offset = self.config.director.latency_offset_ms / 1000.0
        scene = cal.tick(time.monotonic(), self.analysis.latest(), offset)
        if scene is not None:
            self.obs.switch_program(scene, 0)
        self._set_status(DirectorStatus.CALIBRATING, cal.message)

    def _execute(self, d: DirectorDecisionEvent, previous: Optional[str]) -> None:
        with self._lock:
            self._last_switch = d
        if self.shadow:
            return
        scene = d.candidate_camera
        req = SceneSwitchRequested(time.monotonic(), scene, d.correlation_id)
        self.decision_log.write("SceneSwitchRequested", req)
        self._inflight[d.correlation_id] = (scene, previous)
        if not self.obs.switch_program(scene, d.correlation_id):
            self._inflight.pop(d.correlation_id, None)
            self.engine.on_switch_failed(scene, previous, time.monotonic())
            self.decision_log.write("SceneSwitchFailed",
                                    SceneSwitchFailed(time.monotonic(), scene, d.correlation_id,
                                                      "OBS not ready or scene missing"))

    def _record(self, d: DirectorDecisionEvent) -> None:
        if d.action == DirectorAction.HOLD:
            # HOLD reasons carry counters ("insert 7s of ~9s planned"): log the same kind of
            # hold at most every 15 s, ignoring the numbers
            kind = re.sub(r"[\d.]+", "#", d.reason)
            if kind == self._last_hold_reason and d.t - self._last_hold_logged < 15.0:
                return
            self._last_hold_reason, self._last_hold_logged = kind, d.t
        else:
            self._last_hold_reason = ""
        self.decisions.append(d)
        self.decision_log.write("DirectorDecision", d)

    # ------------------------------------------------------------------ UI snapshot
    def snapshot(self) -> AppSnapshot:
        e = self.engine
        now = time.monotonic()
        music = self.analysis.latest()
        audio = self.audio.status()
        b = self.audio.backend
        return AppSnapshot(
            director=self.status, director_message=self.message, shadow=self.shadow,
            current_camera=e.current, time_on_shot=now - e.shot_start if e.current else 0.0,
            last_switch=self._last_switch, last_decision=e.last_decision,
            pending_target=e.pending.target if e.pending else None,
            audio=audio, music=music, obs=self.obs.status(),
            latency=LatencyReport(capture_ms=audio.capture_latency_ms,
                                  analysis_ms=music.analysis_latency_ms if music else 0.0,
                                  dsp_cost_ms=self.analysis.processing_ms,
                                  decision_ms=self._decision_ms,
                                  command_rtt_ms=self.obs.last_round_trip_ms,
                                  offset_ms=self.config.director.latency_offset_ms),
            preset=self.config.director.preset,
            cuts_last_minute=sum(1 for t in e.cut_times if now - t < 60),
            override_active=now < e.override_until,
            coverage=self._coverage(now),
            vision=self.vision.status(),
            reveal_camera=self._reveal_name())

    def _coverage(self, now: float) -> tuple:
        e = self.engine
        if not e.history:
            return ()
        try:
            shares, targets = e.coverage(now, prior=False), e.coverage_targets()
        except Exception:
            return ()
        return tuple((k, shares.get(k, 0.0), targets[k]) for k in ("master", "detail", "side") if k in targets)
