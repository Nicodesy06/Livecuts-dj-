"""A small fake obs-websocket 5.x server for tests and demos (dev dependency: websockets).

Implements Hello/Identify (with optional authentication), the requests DJ
Director uses, and the events it listens to. It can also simulate operator
actions (manual scene change, rename, removal) and OBS shutting down.

Run standalone:  python -m dj_director.obs.fake_server --port 4455 --password secret
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import secrets
import threading
import uuid
from typing import Optional

from websockets.sync.server import serve


class FakeObsServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, password: str = "",
                 scenes: Optional[list[str]] = None, studio_mode: bool = False) -> None:
        self.password = password
        self.scenes = [{"sceneName": n, "sceneUuid": str(uuid.uuid4())}
                       for n in (scenes or ["Cam Master", "Cam Mixer Detail", "Cam Side"])]
        self.program = self.scenes[0]["sceneName"]
        self.preview = self.scenes[0]["sceneName"]
        self.studio_mode = studio_mode
        self.requests: list[tuple[str, dict]] = []
        self.fail_switches = False
        self.response_delay = 0.0
        self.refuse_connections = False      # simulate OBS being closed
        self.obs_version = "31.0.0-fake"
        self.ws_version = "5.5.0-fake"
        self.collection = "Fake Collection"
        # GetSourceScreenshot: per-scene look of the synthetic still (see _screenshot)
        self.scene_look: dict[str, dict] = {}
        # 0.6: one full-canvas camera item (+ a small logo) per scene; transitions
        self.items: dict[str, list[dict]] = {}
        self.transform_log: list[tuple[str, int, dict]] = []
        self.transitions = [{"transitionName": "Cut", "transitionKind": "cut_transition", "transitionFixed": True},
                            {"transitionName": "Fade", "transitionKind": "fade_transition",
                             "transitionFixed": False},
                            {"transitionName": "Stinger DJ", "transitionKind": "obs_stinger_transition",
                             "transitionFixed": False}]
        self.transition = "Cut"
        self.transition_duration = 300
        self.stinger_settings = {"path": "C:/stinger.webm", "transition_point": 450, "tp_type": 0}
        self.program_transitions: list[tuple[str, str]] = []      # (scene, transition used)
        from dj_director.visual.imaging import warm_up
        warm_up()
        self._clients = set()
        self._lock = threading.Lock()
        self._server = serve(self._handler, host, port)
        self.port = self._server.socket.getsockname()[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> "FakeObsServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self.disconnect_all()
        self._server.shutdown()
        self._thread.join(timeout=3)

    # ------------------------------------------------------------------ simulation helpers
    def broadcast(self, event_type: str, data: dict) -> None:
        msg = json.dumps({"op": 5, "d": {"eventType": event_type, "eventIntent": 4, "eventData": data}})
        for ws in list(self._clients):
            try:
                ws.send(msg)
            except Exception:
                pass

    def operator_switch(self, name: str) -> None:
        self.program = name
        self.broadcast("CurrentProgramSceneChanged", {"sceneName": name, "sceneUuid": self._uuid(name)})

    def silent_switch(self, name: str) -> None:
        """Program changes but the event is lost (tests the periodic reconciliation)."""
        self.program = name

    def rename_scene(self, old: str, new: str) -> None:
        for s in self.scenes:
            if s["sceneName"] == old:
                s["sceneName"] = new
        if self.program == old:
            self.program = new
        self.broadcast("SceneNameChanged", {"oldSceneName": old, "sceneName": new,
                                            "sceneUuid": self._uuid(new)})

    def remove_scene(self, name: str) -> None:
        u = self._uuid(name)
        self.scenes = [s for s in self.scenes if s["sceneName"] != name]
        self.broadcast("SceneRemoved", {"sceneName": name, "sceneUuid": u})

    def disconnect_all(self) -> None:
        clients = list(self._clients)
        self._clients.clear()                # stop broadcasting to sockets being closed
        for ws in clients:
            try:
                ws.close()
            except Exception:
                pass

    def scene_items(self, name: str) -> list[dict]:
        if name not in self.items:
            cam = {"sceneItemId": 1, "sourceName": f"{name} source", "sceneItemEnabled": True, "sceneItemIndex": 0,
                   "sceneItemTransform": {"positionX": 0.0, "positionY": 0.0, "rotation": 0.0, "scaleX": 1.0,
                                          "scaleY": 1.0, "alignment": 5, "boundsType": "OBS_BOUNDS_NONE",
                                          "boundsWidth": 0.0, "boundsHeight": 0.0, "boundsAlignment": 0,
                                          "sourceWidth": 1920.0, "sourceHeight": 1080.0, "width": 1920.0,
                                          "height": 1080.0, "cropLeft": 0, "cropRight": 0, "cropTop": 0,
                                          "cropBottom": 0}}
            logo = {"sceneItemId": 2, "sourceName": "Logo", "sceneItemEnabled": True, "sceneItemIndex": 1,
                    "sceneItemTransform": {"positionX": 1700.0, "positionY": 40.0, "rotation": 0.0, "scaleX": 1.0,
                                           "scaleY": 1.0, "alignment": 5, "boundsType": "OBS_BOUNDS_NONE",
                                           "sourceWidth": 180.0, "sourceHeight": 80.0, "width": 180.0,
                                           "height": 80.0}}
            self.items[name] = [cam, logo]
        return self.items[name]

    def _uuid(self, name: str) -> str:
        return next((s["sceneUuid"] for s in self.scenes if s["sceneName"] == name), "")

    # ------------------------------------------------------------------ protocol
    def _handler(self, ws) -> None:
        if self.refuse_connections:
            ws.close(1001, "OBS is not running")
            return
        salt, challenge = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
        hello = {"obsWebSocketVersion": self.ws_version, "rpcVersion": 1}
        if self.password:
            hello["authentication"] = {"challenge": challenge, "salt": salt}
        ws.send(json.dumps({"op": 0, "d": hello}))
        ident = json.loads(ws.recv())
        if ident.get("op") != 1:
            ws.close(4000, "expected Identify")
            return
        if self.password:
            secret = base64.b64encode(hashlib.sha256((self.password + salt).encode()).digest()).decode()
            expected = base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()
            if ident["d"].get("authentication") != expected:
                ws.close(4009, "Authentication failed.")
                return
        ws.send(json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}))
        self._clients.add(ws)
        try:
            for raw in ws:
                msg = json.loads(raw)
                if msg.get("op") == 6:
                    self._request(ws, msg["d"])
        except Exception:
            pass
        finally:
            self._clients.discard(ws)

    def _request(self, ws, d: dict) -> None:
        import time
        rtype, rid, data = d["requestType"], d["requestId"], d.get("requestData", {}) or {}
        with self._lock:
            self.requests.append((rtype, data))
        if self.response_delay:
            time.sleep(self.response_delay)
        ok, code, comment, resp = True, 100, "", {}
        if rtype == "GetVersion":
            resp = {"obsVersion": self.obs_version, "obsWebSocketVersion": self.ws_version, "rpcVersion": 1}
        elif rtype == "GetSceneList":
            resp = {"currentProgramSceneName": self.program, "currentProgramSceneUuid": self._uuid(self.program),
                    "currentPreviewSceneName": self.preview if self.studio_mode else None,
                    "scenes": [dict(s, sceneIndex=i) for i, s in enumerate(reversed(self.scenes))]}
        elif rtype == "GetStudioModeEnabled":
            resp = {"studioModeEnabled": self.studio_mode}
        elif rtype == "GetSceneCollectionList":
            resp = {"currentSceneCollectionName": self.collection, "sceneCollections": [self.collection]}
        elif rtype == "SetCurrentProgramScene":
            name = data.get("sceneName") or next(
                (s["sceneName"] for s in self.scenes if s["sceneUuid"] == data.get("sceneUuid")), None)
            if self.fail_switches:
                ok, code, comment = False, 500, "simulated failure"
            elif name is None or not self._uuid(name):
                ok, code, comment = False, 600, "No source was found"
            else:
                self.program = name
                self.program_transitions.append((name, self.transition))
                self.broadcast("CurrentProgramSceneChanged", {"sceneName": name, "sceneUuid": self._uuid(name)})
        elif rtype in ("GetSceneItemList", "GetSceneItemTransform", "SetSceneItemTransform"):
            name = data.get("sceneName", "")
            if not self._uuid(name):
                ok, code, comment = False, 600, "No source was found"
            elif rtype == "GetSceneItemList":
                resp = {"sceneItems": json.loads(json.dumps(self.scene_items(name)))}
            else:
                it = next((i for i in self.scene_items(name) if i["sceneItemId"] == data.get("sceneItemId")), None)
                if it is None:
                    ok, code, comment = False, 600, "No scene item was found"
                elif rtype == "GetSceneItemTransform":
                    resp = {"sceneItemTransform": dict(it["sceneItemTransform"])}
                else:
                    t = it["sceneItemTransform"]
                    t.update(data.get("sceneItemTransform") or {})
                    t["width"] = t["sourceWidth"] * t["scaleX"]
                    t["height"] = t["sourceHeight"] * t["scaleY"]
                    self.transform_log.append((name, it["sceneItemId"], dict(data.get("sceneItemTransform") or {})))
        elif rtype == "GetVideoSettings":
            resp = {"fpsNumerator": 60, "fpsDenominator": 1, "baseWidth": 1920, "baseHeight": 1080,
                    "outputWidth": 1920, "outputHeight": 1080}
        elif rtype == "GetSceneTransitionList":
            resp = {"currentSceneTransitionName": self.transition, "transitions": list(self.transitions)}
        elif rtype == "GetCurrentSceneTransition":
            tr = next(t for t in self.transitions if t["transitionName"] == self.transition)
            resp = {"transitionName": tr["transitionName"], "transitionKind": tr["transitionKind"],
                    "transitionFixed": tr["transitionFixed"],
                    "transitionDuration": None if tr["transitionFixed"] else self.transition_duration,
                    "transitionSettings": dict(self.stinger_settings)
                    if tr["transitionKind"] == "obs_stinger_transition" else {}}
        elif rtype == "SetCurrentSceneTransition":
            if any(t["transitionName"] == data.get("transitionName") for t in self.transitions):
                self.transition = data["transitionName"]
            else:
                ok, code, comment = False, 600, "No transition was found"
        elif rtype == "GetSourceScreenshot":
            name = data.get("sourceName", "")
            if not self._uuid(name):
                ok, code, comment = False, 600, "No source was found"
            else:
                resp = {"imageData": self._screenshot(name, int(data.get("imageWidth") or 384),
                                                      int(data.get("imageHeight") or 216))}
        else:
            ok, code, comment = False, 204, "Unknown request type"
        ws.send(json.dumps({"op": 7, "d": {"requestType": rtype, "requestId": rid,
                                           "requestStatus": {"result": ok, "code": code, "comment": comment},
                                           "responseData": resp}}))


    def _screenshot(self, name: str, w: int, h: int) -> str:
        """Synthetic camera still: textured background + a moving block.

        scene_look[name] keys: brightness (0..1), motion (0..1 block speed),
        pulse_period (s; >0 = whole frame pulses on that period), frozen (bool),
        blur (bool) / blur_at (monotonic time focus is lost), black (bool),
        roi_motion ((x, y, w, h), motion confined there).
        """
        import time
        import numpy as np
        from dj_director.visual.imaging import encode
        look = self.scene_look.get(name, {})
        t = look.get("frozen_t") if look.get("frozen") else time.monotonic()
        if look.get("frozen") and t is None:
            t = look["frozen_t"] = time.monotonic()
        seed = sum(ord(c) for c in name)
        rng = np.random.default_rng(seed)
        base = rng.integers(40, 200, size=(h // 8 + 1, w // 8 + 1, 3)).astype(np.float32)
        img = np.repeat(np.repeat(base, 8, 0), 8, 1)[:h, :w]
        if look.get("blur") or (look.get("blur_at") and time.monotonic() >= look["blur_at"]):
            img[:] = img.mean(axis=(0, 1))
        speed = float(look.get("motion", 0.3))
        rx, ry, rw, rh = look.get("roi_motion", (0.0, 0.0, 1.0, 1.0))
        bw, bh = max(4, int(w * rw / 4)), max(4, int(h * rh / 3))
        span = max(1, int(w * rw) - bw)
        x = int(w * rx) + int((math.sin(t * speed * 6.0) * 0.5 + 0.5) * span) if speed > 0 else int(w * rx)
        y = int(h * ry) + int(h * rh / 3)
        img[y:y + bh, x:x + bw] = (250, 240, 60)
        if look.get("pulse_period"):
            img *= 0.75 + 0.25 * math.cos(2 * math.pi * t / look["pulse_period"])
        img *= float(look.get("brightness", 1.0))
        if look.get("black"):
            img[:] = 0
        data = encode(np.clip(img, 0, 255).astype(np.uint8), "JPG", 80)
        return "data:image/jpg;base64," + base64.b64encode(data).decode()


def main() -> None:
    ap = argparse.ArgumentParser(description="Fake OBS websocket server for DJ Director testing")
    ap.add_argument("--port", type=int, default=4455)
    ap.add_argument("--password", default="")
    ap.add_argument("--scenes", default="Cam Master,Cam Mixer Detail,Cam Side,Cam Wide")
    a = ap.parse_args()
    srv = FakeObsServer(port=a.port, password=a.password, scenes=a.scenes.split(",")).start()
    print(f"fake OBS listening on ws://127.0.0.1:{srv.port}  (Ctrl+C to stop)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.stop()


if __name__ == "__main__":
    main()
