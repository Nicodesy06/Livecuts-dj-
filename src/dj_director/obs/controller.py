"""ObsController: connection supervision, scene state and safe scene switching.

* Supervisor thread connects, re-validates scenes after every (re)connect and
  reconnects with capped exponential backoff. Authentication failures stop
  automatic retries until the configuration changes (no password hammering).
* Scene switches go through a single-slot command queue processed by a
  dedicated thread, so the director never blocks on the network; a newer
  command replaces an unsent older one.
* Studio Mode: switches always target the **Program** scene
  (``SetCurrentProgramScene``). Preview is never touched. This is the
  documented behaviour of the auto director: what it chooses goes on air.
* Manual override detection: every program change we request is remembered
  for a short window; a ``CurrentProgramSceneChanged`` event that does not
  match is reported as an external (manual) change.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from dj_director.config.schema import ObsConfig
from dj_director.core.events import (ExternalSceneChange, ObsStatusEvent, SceneSwitchConfirmed,
                                     SceneSwitchFailed)
from dj_director.obs.client import ObsAuthError, ObsClient, ObsError

log = logging.getLogger(__name__)
EXPECT_WINDOW = 3.0   # s within which a program change is attributed to our own command


@dataclass(frozen=True)
class SceneInfo:
    name: str
    uuid: str = ""


class ObsController:
    def __init__(self, cfg: ObsConfig, password_provider: Callable[[], str]) -> None:
        self.cfg = cfg
        self.password_provider = password_provider
        self.client = ObsClient(on_event=self._on_event, on_disconnect=self._on_disconnect)
        self.on_status: Callable[[ObsStatusEvent], None] = lambda e: None
        self.on_external_change: Callable[[ExternalSceneChange], None] = lambda e: None
        self.on_scene_renamed: Callable[[str, str], None] = lambda old, new: None
        self.on_scenes_changed: Callable[[list[SceneInfo]], None] = lambda s: None
        self.on_switch_result: Callable[[object], None] = lambda e: None
        self._lock = threading.RLock()
        self.state = "disconnected"
        self.error = ""
        self.scenes: list[SceneInfo] = []
        self.program = ""
        self.preview = ""
        self.studio_mode = False
        self.collection = ""
        self.obs_version = ""
        self.scenes_valid = False
        self._expected: list[tuple[str, float]] = []
        self._want = False
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._supervisor: Optional[threading.Thread] = None
        self._cmd_lock = threading.Condition()
        self._cmd: Optional[tuple[str, int, float]] = None
        self._cmd_thread: Optional[threading.Thread] = None
        self.last_round_trip_ms = 0.0

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._want = True
        self._stop.clear()
        if self._supervisor is None or not self._supervisor.is_alive():
            self._supervisor = threading.Thread(target=self._supervise, name="obs-supervisor", daemon=True)
            self._supervisor.start()
        if self._cmd_thread is None or not self._cmd_thread.is_alive():
            self._cmd_thread = threading.Thread(target=self._command_loop, name="obs-commands", daemon=True)
            self._cmd_thread.start()
        self._wake.set()

    def stop(self) -> None:
        self._want = False
        self._stop.set()
        self._wake.set()
        with self._cmd_lock:
            self._cmd = None
            self._cmd_lock.notify_all()
        self.client.close()
        for t in (self._supervisor, self._cmd_thread):
            if t is not None:
                t.join(timeout=3)
        self._supervisor = self._cmd_thread = None
        self._set_state("disconnected")

    def update_config(self, cfg: ObsConfig) -> None:
        self.cfg = cfg
        self.reconnect_now()

    def reconnect_now(self) -> None:
        self.client.close()
        if self.state == "auth_failed":
            self.state = "disconnected"
        self._wake.set()

    @property
    def connected(self) -> bool:
        return self.client.connected and self.state == "connected"

    # ------------------------------------------------------------------ status
    def status(self) -> ObsStatusEvent:
        with self._lock:
            return ObsStatusEvent(t=time.monotonic(), connected=self.connected, state=self.state,
                                  obs_version=self.obs_version, ws_version=self.client.server_version,
                                  scene_collection=self.collection, program_scene=self.program,
                                  preview_scene=self.preview, studio_mode=self.studio_mode,
                                  scenes=tuple(s.name for s in self.scenes), error=self.error)

    def _set_state(self, state: str, error: str = "") -> None:
        with self._lock:
            self.state = state
            self.error = error
        self.on_status(self.status())

    # ------------------------------------------------------------------ supervisor
    def _supervise(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            if self._want and not self.client.connected and self.state != "auth_failed":
                self._set_state("connecting")
                try:
                    self.client.connect(self.cfg.host, self.cfg.port, self.password_provider(),
                                        self.cfg.connect_timeout)
                    self.refresh()
                    self._set_state("connected")
                    log.info("connected to OBS %s (websocket %s)", self.obs_version,
                             self.client.server_version)
                    backoff = 1.0
                except ObsAuthError as e:
                    log.warning("OBS authentication failed: %s", e)
                    self._set_state("auth_failed", str(e))
                except (ObsError, TimeoutError, OSError) as e:
                    self.client.close()
                    self._set_state("disconnected", str(e))
                    log.info("OBS not reachable (%s); retry in %.0fs", e, backoff)
                    self._wake.wait(backoff)
                    self._wake.clear()
                    backoff = min(backoff * 2, 15.0)
                    continue
            self._wake.wait(1.0)
            self._wake.clear()

    def refresh(self) -> None:
        """Re-read version, scenes, program/preview and studio mode."""
        t = self.cfg.request_timeout
        ver = self.client.request("GetVersion", timeout=t)
        lst = self.client.request("GetSceneList", timeout=t)
        studio = self.client.request("GetStudioModeEnabled", timeout=t)
        try:
            coll = self.client.request("GetSceneCollectionList", timeout=t).get(
                "currentSceneCollectionName", "")
        except ObsError:
            coll = ""
        scenes = [SceneInfo(s.get("sceneName", ""), s.get("sceneUuid", "") or "")
                  for s in reversed(lst.get("scenes", []))]   # OBS lists bottom-up
        with self._lock:
            self.obs_version = ver.get("obsVersion", "")
            self.scenes = scenes
            self.program = lst.get("currentProgramSceneName", "") or ""
            self.preview = lst.get("currentPreviewSceneName", "") or ""
            self.studio_mode = bool(studio.get("studioModeEnabled", False))
            self.collection = coll
            self.scenes_valid = True
        self.on_scenes_changed(list(scenes))

    # ------------------------------------------------------------------ events (reader thread)
    def _on_event(self, etype: str, data: dict) -> None:
        if etype == "CurrentProgramSceneChanged":
            name = data.get("sceneName", "")
            with self._lock:
                self.program = name
                now = time.monotonic()
                self._expected = [(n, t) for n, t in self._expected if now - t < EXPECT_WINDOW]
                ours = any(n == name for n, _ in self._expected)
                if ours:
                    self._expected = [(n, t) for n, t in self._expected if n != name]
            if not ours:
                log.info("external program change -> %s", name)
                self.on_external_change(ExternalSceneChange(time.monotonic(), name))
            self.on_status(self.status())
        elif etype == "CurrentPreviewSceneChanged":
            with self._lock:
                self.preview = data.get("sceneName", "")
        elif etype == "StudioModeStateChanged":
            with self._lock:
                self.studio_mode = bool(data.get("studioModeEnabled", False))
            self.on_status(self.status())
        elif etype == "SceneNameChanged":
            old, new = data.get("oldSceneName", ""), data.get("sceneName", "")
            with self._lock:
                self.scenes = [SceneInfo(new, s.uuid) if s.name == old else s for s in self.scenes]
                if self.program == old:
                    self.program = new
            self.on_scene_renamed(old, new)
            self.on_scenes_changed(list(self.scenes))
        elif etype in ("SceneCreated", "SceneRemoved", "SceneListChanged"):
            if etype == "SceneListChanged" and "scenes" in data:
                with self._lock:
                    self.scenes = [SceneInfo(s.get("sceneName", ""), s.get("sceneUuid", "") or "")
                                   for s in reversed(data["scenes"])]
            elif etype == "SceneRemoved":
                name = data.get("sceneName", "")
                with self._lock:
                    self.scenes = [s for s in self.scenes if s.name != name]
            elif etype == "SceneCreated":
                with self._lock:
                    self.scenes = self.scenes + [SceneInfo(data.get("sceneName", ""),
                                                           data.get("sceneUuid", "") or "")]
            self.on_scenes_changed(list(self.scenes))
            self.on_status(self.status())
        elif etype == "CurrentSceneCollectionChanging":
            with self._lock:
                self.scenes_valid = False
            self.on_scenes_changed([])
            self.on_status(self.status())
        elif etype == "CurrentSceneCollectionChanged":
            # refresh off the reader thread (requests need the reader to receive responses)
            threading.Thread(target=self._safe_refresh, name="obs-refresh", daemon=True).start()
        elif etype == "ExitStarted":
            log.info("OBS is shutting down")
            with self._lock:
                self.scenes_valid = False

    def _safe_refresh(self) -> None:
        try:
            self.refresh()
            self.on_status(self.status())
        except Exception as e:
            log.warning("scene refresh failed: %s", e)

    def _on_disconnect(self, reason: str) -> None:
        with self._lock:
            self.scenes_valid = False
        log.warning("OBS disconnected: %s", reason)
        if self._want:
            self._set_state("disconnected", reason)
            self._wake.set()

    # ------------------------------------------------------------------ switching
    def scene_exists(self, name: str) -> bool:
        with self._lock:
            return any(s.name == name for s in self.scenes)

    def can_switch(self) -> bool:
        return self.connected and self.scenes_valid

    def switch_program(self, scene: str, correlation_id: int) -> bool:
        """Queue a Program scene change. Returns False if it cannot be attempted now."""
        if not self.can_switch() or not self.scene_exists(scene):
            return False
        with self._cmd_lock:
            self._cmd = (scene, correlation_id, time.monotonic())
            self._cmd_lock.notify_all()
        return True

    def _command_loop(self) -> None:
        while not self._stop.is_set():
            with self._cmd_lock:
                while self._cmd is None and not self._stop.is_set():
                    self._cmd_lock.wait(0.5)
                if self._stop.is_set():
                    return
                scene, cid, queued = self._cmd
                self._cmd = None
            if time.monotonic() - queued > 2.0:
                self.on_switch_result(SceneSwitchFailed(time.monotonic(), scene, cid, "command expired"))
                continue
            with self._lock:
                uuid = next((s.uuid for s in self.scenes if s.name == scene), "")
                self._expected.append((scene, time.monotonic()))
            data = {"sceneUuid": uuid} if uuid else {"sceneName": scene}
            t0 = time.perf_counter()
            try:
                self.client.request("SetCurrentProgramScene", data, timeout=self.cfg.request_timeout)
                rt = (time.perf_counter() - t0) * 1000
                self.last_round_trip_ms = rt
                self.on_switch_result(SceneSwitchConfirmed(time.monotonic(), scene, cid, rt))
            except Exception as e:
                with self._lock:
                    self._expected = [(n, t) for n, t in self._expected if n != scene]
                log.warning("scene switch to %s failed: %s", scene, e)
                self.on_switch_result(SceneSwitchFailed(time.monotonic(), scene, cid, str(e)))

    # ------------------------------------------------------------------ one-shot test (wizard)
    @staticmethod
    def test_connection(host: str, port: int, password: str, timeout: float = 4.0) -> tuple[bool, str, list[str]]:
        c = ObsClient()
        try:
            c.connect(host, port, password, timeout)
            ver = c.request("GetVersion", timeout=timeout)
            lst = c.request("GetSceneList", timeout=timeout)
            names = [s.get("sceneName", "") for s in reversed(lst.get("scenes", []))]
            return True, f"OBS {ver.get('obsVersion', '?')} (websocket {c.server_version})", names
        except ObsAuthError as e:
            return False, str(e), []
        except Exception as e:
            return False, str(e), []
        finally:
            c.close()
