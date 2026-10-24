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
                self.broadcast("CurrentProgramSceneChanged", {"sceneName": name, "sceneUuid": self._uuid(name)})
        else:
            ok, code, comment = False, 204, "Unknown request type"
        ws.send(json.dumps({"op": 7, "d": {"requestType": rtype, "requestId": rid,
                                           "requestStatus": {"result": ok, "code": code, "comment": comment},
                                           "responseData": resp}}))


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
