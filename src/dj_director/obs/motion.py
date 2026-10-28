"""Light zoom between shots (0.6, opt-in): the camera's scene item is scaled for a fraction of a second.

OBS has no built-in zoom transition, so the zoom is made on the camera itself:
in every camera scene the largest visible item (the camera) is scaled about its
own centre through ``SetSceneItemTransform``.

* **through**: the outgoing shot pushes in (1 -> 1 + a, accelerating) during
  the last fraction of a beat, the cut happens, and the incoming shot - set to
  1 + a while still off air - settles back to 1 (decelerating). The classic
  "zoom through" of club edits, kept light (default a = 4 %).
* **settle**: only the incoming shot settles from 1 + a.

The scale never goes below 1, so no border ever shows. Overlays (logos, text)
in the scene are left alone. An outgoing scene is put back exactly as found once
it is off air. The transforms found are also written to a small file, so a
crash or a lost connection mid-zoom is repaired at the next start.

Runs on its own obs-websocket connection limited to ``MOTION_REQUESTS``; a
failure only disables the zoom for that scene, never the cuts.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from dj_director.obs import protocol as P
from dj_director.obs.client import ObsAuthError, ObsClient

log = logging.getLogger(__name__)

ALIGN_LEFT, ALIGN_RIGHT, ALIGN_TOP, ALIGN_BOTTOM = 1, 2, 4, 8
BOUNDS_NONE = "OBS_BOUNDS_NONE"
FPS = 50.0
KEYS_SCALE = ("positionX", "positionY", "scaleX", "scaleY")
KEYS_BOUNDS = ("positionX", "positionY", "boundsWidth", "boundsHeight")


# ----------------------------------------------------------------------------- pure geometry
def _bounded(t: dict) -> bool:
    return (t.get("boundsType") or BOUNDS_NONE) != BOUNDS_NONE


def box_size(t: dict) -> tuple[float, float]:
    """Size of the item on the canvas (its bounding box when it has one)."""
    if _bounded(t):
        return float(t.get("boundsWidth", 0) or 0), float(t.get("boundsHeight", 0) or 0)
    w = t.get("width")
    h = t.get("height")
    if not w or not h:
        w = float(t.get("sourceWidth", 0) or 0) * float(t.get("scaleX", 1) or 1)
        h = float(t.get("sourceHeight", 0) or 0) * float(t.get("scaleY", 1) or 1)
    return float(w), float(h)


def zoom_fields(t: dict, k: float) -> Optional[dict]:
    """Transform fields that scale the item by ``k`` about its own centre (None: cannot zoom it).

    Every point x of the item maps to C + (x - C) k: the alignment point (the item's
    position) moves the same way and the size scales by k.
    """
    if abs(float(t.get("rotation", 0) or 0)) > 0.01:
        return None
    w, h = box_size(t)
    if w <= 0 or h <= 0:
        return None
    al = int(t.get("alignment", ALIGN_LEFT | ALIGN_TOP))
    px, py = float(t.get("positionX", 0)), float(t.get("positionY", 0))
    cx = px + (w / 2 if al & ALIGN_LEFT else -w / 2 if al & ALIGN_RIGHT else 0.0)
    cy = py + (h / 2 if al & ALIGN_TOP else -h / 2 if al & ALIGN_BOTTOM else 0.0)
    out = {"positionX": cx + (px - cx) * k, "positionY": cy + (py - cy) * k}
    if _bounded(t):
        out["boundsWidth"] = max(1.0, float(t["boundsWidth"]) * k)
        out["boundsHeight"] = max(1.0, float(t["boundsHeight"]) * k)
    else:
        out["scaleX"] = float(t.get("scaleX", 1)) * k
        out["scaleY"] = float(t.get("scaleY", 1)) * k
    return out


def original_fields(t: dict) -> dict:
    return {k: t[k] for k in (KEYS_BOUNDS if _bounded(t) else KEYS_SCALE) if k in t}


def pick_item(items: list) -> Optional[dict]:
    """The camera in a scene: the largest visible, unrotated item (overlays are smaller)."""
    best, area = None, 0.0
    for it in items:
        if not it.get("sceneItemEnabled", True):
            continue
        t = it.get("sceneItemTransform") or {}
        if abs(float(t.get("rotation", 0) or 0)) > 0.01:
            continue
        w, h = box_size(t)
        if w * h > area:
            best, area = it, w * h
    return best


def ease_in(x: float) -> float:
    return x * x * x


def ease_out(x: float) -> float:
    return 1.0 - (1.0 - x) ** 3


def zoom_timing(beat_period: float) -> tuple[float, float]:
    """(push, settle) durations: half a beat and one beat, kept within a pleasant range."""
    if beat_period <= 0:
        return 0.3, 0.6
    return min(max(beat_period * 0.5, 0.2), 0.45), min(max(beat_period, 0.35), 0.9)


# ----------------------------------------------------------------------------- animator
class _Scene:
    __slots__ = ("item_id", "orig", "k")

    def __init__(self, item_id: int, orig: dict) -> None:
        self.item_id, self.orig, self.k = item_id, orig, 1.0


class ZoomAnimator:
    def __init__(self, host: str, port: int, password_provider: Callable[[], str], restore_file: Path,
                 timeout: float = 1.0) -> None:
        self.host, self.port = host, port
        self.password_provider = password_provider
        self.restore_file = Path(restore_file)
        self.timeout = timeout
        self.client = ObsClient(event_subscriptions=0, allowed=P.MOTION_REQUESTS)
        self.state = "off"
        self.error = ""
        self.frames_sent = 0
        self._q: queue.Queue = queue.Queue()
        self._scenes: dict[str, _Scene] = {}
        self._anims: dict[str, tuple] = {}         # scene -> (t0, dur, k0, k1, ease)
        self._resets: list[tuple[float, str]] = []  # (due, scene): put back once off air
        self._skip: dict[str, float] = {}           # scenes that cannot be zoomed (until)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self.state = "connecting"
        self._thread = threading.Thread(target=self._run, name="obs-zoom", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._q.put(("restore_all",))
        self._q.put(("quit",))
        self._thread.join(timeout=3)
        self._thread = None
        self._stop.set()
        self.client.close()
        self.state = "off"

    # ------------------------------------------------------------------ commands (any thread, never block)
    def prepare(self, scene: str, amount: float) -> None:
        """Off-air scene set to 1 + a, ready to settle at the cut."""
        self._q.put(("anim", scene, 0.0, None, 1.0 + amount, ease_out))

    def push(self, scene: str, amount: float, duration: float) -> None:
        """On-air scene pushes in to 1 + a (the cut follows)."""
        self._q.put(("anim", scene, duration, None, 1.0 + amount, ease_in))

    def settle(self, scene: str, duration: float) -> None:
        """Scene that just went on air eases back to 1 from wherever it is."""
        self._q.put(("anim", scene, duration, None, 1.0, ease_out))

    def reset(self, scene: str, delay: float = 0.0) -> None:
        """Put the scene back exactly as found (instant: only for scenes off air)."""
        self._q.put(("reset", scene, delay))

    def restore_all(self) -> None:
        self._q.put(("restore_all",))

    def zoomed(self) -> list[str]:
        return [n for n, s in list(self._scenes.items()) if abs(s.k - 1.0) > 1e-4]

    # ------------------------------------------------------------------ worker
    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            if not self.client.connected:
                try:
                    self.client.connect(self.host, self.port, self.password_provider(), timeout=3.0)
                    self.state, self.error, backoff = "running", "", 1.0
                    self._scenes.clear()
                    self._repair_from_file()
                except ObsAuthError as e:
                    self.state, self.error = "auth_failed", str(e)
                    return
                except Exception as e:
                    self.state, self.error = "connecting", str(e)
                    if self._drain(backoff):
                        return
                    backoff = min(backoff * 2, 15.0)
                    continue
            wait = 1.0 / FPS if (self._anims or self._resets) else 0.25
            if self._drain(wait):
                return
            try:
                self._step(time.monotonic())
            except Exception as e:
                log.warning("zoom step failed: %s", e)
                self._anims.clear()
                if not self.client.connected:
                    self.state = "connecting"

    def _drain(self, wait: float) -> bool:
        """Process queued commands for up to ``wait`` s. True when asked to quit."""
        deadline = time.monotonic() + wait
        while True:
            try:
                cmd = self._q.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                return False
            if cmd[0] == "quit":
                return True
            try:
                self._handle(cmd, time.monotonic())
            except Exception as e:
                log.warning("zoom command %s failed: %s", cmd[0], e)
            if self._anims or self._resets:      # an animation just started: first frame now, not later
                deadline = min(deadline, time.monotonic() + 1.0 / FPS)

    def _handle(self, cmd: tuple, now: float) -> None:
        if not self.client.connected:
            return
        kind, scene = cmd[0], cmd[1] if len(cmd) > 1 else None
        if kind == "anim":
            _, scene, dur, _k0, k1, ease = cmd
            st = self._scene(scene, now)
            if st is None:
                return
            self._resets = [(d, s) for d, s in self._resets if s != scene]
            if dur <= 0:
                self._anims.pop(scene, None)
                self._apply(scene, st, k1)
            else:
                self._anims[scene] = (now, dur, st.k, k1, ease)
        elif kind == "reset":
            self._anims.pop(scene, None)
            self._resets.append((now + cmd[2], scene))
        elif kind == "restore_all":
            self._anims.clear()
            self._resets.clear()
            for name, st in list(self._scenes.items()):
                if abs(st.k - 1.0) > 1e-6:
                    self._apply(name, st, 1.0)

    def _step(self, now: float) -> None:
        for scene, (t0, dur, k0, k1, ease) in list(self._anims.items()):
            st = self._scenes.get(scene)
            if st is None:
                self._anims.pop(scene, None)
                continue
            x = min(1.0, (now - t0) / dur)
            self._apply(scene, st, k0 + (k1 - k0) * ease(x))
            if x >= 1.0:
                self._anims.pop(scene, None)
        due = [s for d, s in self._resets if d <= now]
        self._resets = [(d, s) for d, s in self._resets if d > now]
        for scene in due:
            st = self._scenes.get(scene)
            if st is not None and abs(st.k - 1.0) > 1e-6:
                self._apply(scene, st, 1.0)

    def _scene(self, scene: str, now: float) -> Optional[_Scene]:
        """The camera item of a scene, with its transform re-read whenever it is at rest."""
        if now < self._skip.get(scene, 0.0):
            return None
        st = self._scenes.get(scene)
        if st is not None and abs(st.k - 1.0) > 1e-6:
            return st
        try:
            items = self.client.request("GetSceneItemList", {"sceneName": scene},
                                        timeout=self.timeout).get("sceneItems", [])
            it = pick_item(items)
            if it is None:
                raise ValueError("no zoomable item")
            t = self.client.request("GetSceneItemTransform", {"sceneName": scene,
                                                              "sceneItemId": it["sceneItemId"]},
                                    timeout=self.timeout).get("sceneItemTransform", {})
            if zoom_fields(t, 1.0) is None:
                raise ValueError("item is rotated or has no size")
        except Exception as e:
            log.info("zoom not possible on %s: %s", scene, e)
            self._skip[scene] = now + 30.0
            self._scenes.pop(scene, None)
            return None
        st = _Scene(int(it["sceneItemId"]), t)
        self._scenes[scene] = st
        return st

    def _apply(self, scene: str, st: _Scene, k: float) -> None:
        if abs(k - st.k) < 1e-5 and abs(k - 1.0) > 1e-6:
            return
        first = abs(st.k - 1.0) <= 1e-6 and abs(k - 1.0) > 1e-6
        fields = original_fields(st.orig) if abs(k - 1.0) <= 1e-6 else zoom_fields(st.orig, k)
        if first:
            self._remember(scene, st)
        self.client.request("SetSceneItemTransform", {"sceneName": scene, "sceneItemId": st.item_id,
                                                      "sceneItemTransform": fields}, timeout=self.timeout)
        self.frames_sent += 1
        st.k = 1.0 if abs(k - 1.0) <= 1e-6 else k
        if st.k == 1.0:
            self._forget(scene)

    # ------------------------------------------------------------------ crash safety
    def _load(self) -> dict:
        try:
            return json.loads(self.restore_file.read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        try:
            if data:
                self.restore_file.parent.mkdir(parents=True, exist_ok=True)
                self.restore_file.write_text(json.dumps(data), "utf-8")
            elif self.restore_file.exists():
                self.restore_file.unlink()
        except OSError:
            log.warning("cannot write %s", self.restore_file)

    def _remember(self, scene: str, st: _Scene) -> None:
        data = self._load()
        data[scene] = {"sceneItemId": st.item_id, "transform": original_fields(st.orig)}
        self._save(data)

    def _forget(self, scene: str) -> None:
        data = self._load()
        if data.pop(scene, None) is not None:
            self._save(data)

    def _repair_from_file(self) -> None:
        """A previous run stopped mid-zoom: put those items back as they were."""
        data = self._load()
        for scene, rec in list(data.items()):
            try:
                self.client.request("SetSceneItemTransform", {"sceneName": scene,
                                                              "sceneItemId": rec["sceneItemId"],
                                                              "sceneItemTransform": rec["transform"]},
                                    timeout=self.timeout)
                log.warning("zoom: restored %s left zoomed by a previous run", scene)
            except Exception as e:
                log.warning("zoom: cannot restore %s: %s", scene, e)
            data.pop(scene, None)
        self._save(data)
