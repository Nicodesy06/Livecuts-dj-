"""Frame grabber: small stills of each camera scene from OBS, on a second connection.

* A dedicated obs-websocket session (no event subscriptions) is used, so
  screenshots never queue in front of the director's scene switches.
* Only ``GetSourceScreenshot`` is sent here (read-only, in the allow-list).
* Budget: ``rate`` requests per second in total (low 5 / normal 10 / high 16).
  The *priority* camera (the drop reveal shot) gets 60 % of them (6 stills/s at
  normal), enough to compare its movement with the beat; the others share the
  rest round-robin (quality and movement need far fewer stills).
* Failures never propagate: authentication errors stop retries until restart,
  missing scenes are skipped for a while, everything else backs off.

OBS note: a camera source that is set to "deactivate when not showing" delivers
no picture while its scene is off air; such cameras simply get no information.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from dj_director.obs.client import ObsAuthError, ObsClient, ObsError, ObsRequestError
from dj_director.visual.imaging import decode_data_uri, to_rgb

log = logging.getLogger(__name__)

RATES = {"low": 5.0, "normal": 10.0, "high": 16.0}
PRIORITY_SHARE = (0, 1, 3)        # of every 5 requests, these turns go to the priority camera (60 %)
FRAME_W, FRAME_H = 384, 216


class FrameGrabber:
    def __init__(self, host: str, port: int, password_provider: Callable[[], str],
                 scenes: Callable[[], list[str]], on_frame: Callable[[str, float, object], None],
                 priority: Callable[[], Optional[str]] = lambda: None, rate: str = "normal",
                 timeout: float = 2.0) -> None:
        self.host, self.port = host, port
        self.password_provider = password_provider
        self.scenes = scenes
        self.on_frame = on_frame
        self.priority = priority
        self.rate = RATES.get(rate, RATES["normal"])
        self.timeout = timeout
        self.client = ObsClient(event_subscriptions=0)
        self.state = "off"
        self.error = ""
        self.frames = 0
        self._skip_until: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._rr = 0
        self._turn = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="vision-frames", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.client.close()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None
        self.state = "off"

    # ------------------------------------------------------------------ loop
    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            if not self.client.connected:
                try:
                    self.state = "connecting"
                    self.client.connect(self.host, self.port, self.password_provider(), self.timeout + 2)
                    self.state, self.error, backoff = "running", "", 1.0
                except ObsAuthError as e:
                    self.state, self.error = "auth_failed", str(e)
                    return
                except (ObsError, OSError) as e:
                    self.state, self.error = "waiting", str(e)
                    self._stop.wait(backoff)
                    backoff = min(backoff * 2, 15.0)
                    continue
            t0 = time.monotonic()
            scene = self._next_scene(t0)
            if scene is not None:
                self._grab(scene)
            self._stop.wait(max(0.0, 1.0 / self.rate - (time.monotonic() - t0)))
        self.client.close()

    def _next_scene(self, now: float) -> Optional[str]:
        names = [s for s in self.scenes() if now >= self._skip_until.get(s, 0.0)]
        if not names:
            return None
        pri = self.priority()
        self._turn += 1
        if pri in names and len(names) > 1 and self._turn % 5 in PRIORITY_SHARE:
            return pri
        others = [s for s in names if s != pri] if pri in names and len(names) > 1 else names
        self._rr = (self._rr + 1) % len(others)
        return others[self._rr]

    def _grab(self, scene: str) -> None:
        try:
            resp = self.client.request("GetSourceScreenshot",
                                       {"sourceName": scene, "imageFormat": "jpg", "imageWidth": FRAME_W,
                                        "imageHeight": FRAME_H, "imageCompressionQuality": 75},
                                       timeout=self.timeout)
        except ObsRequestError as e:
            log.info("no screenshot for scene %s (%s); retry in 10s", scene, e.comment or e.code)
            self._skip_until[scene] = time.monotonic() + 10.0
            return
        except (ObsError, TimeoutError) as e:
            self.error = str(e)
            return
        img = decode_data_uri(resp.get("imageData", ""))
        if img is None:
            return
        self.frames += 1
        self.on_frame(scene, time.monotonic(), to_rgb(img))
