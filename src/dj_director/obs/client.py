"""Minimal, thread-safe obs-websocket 5.x client on top of websocket-client.

Why not a higher-level wrapper library: we need precise control over
timeouts, close codes (authentication failure), event dispatch threading and
a hard allow-list of requests. The protocol subset is small and documented.

Threading: one reader thread per connection; requests may be issued from any
thread and wait on a per-request event with a timeout. Event callbacks run on
the reader thread and must return quickly.
"""

from __future__ import annotations

import itertools
import json
import logging
import threading
from typing import Any, Callable, Optional

import websocket

from dj_director.obs import protocol as P

log = logging.getLogger(__name__)


class ObsError(Exception):
    pass


class ObsAuthError(ObsError):
    pass


class ObsRequestError(ObsError):
    def __init__(self, request_type: str, code: int, comment: str) -> None:
        super().__init__(f"{request_type} failed ({code}): {comment}")
        self.code = code
        self.comment = comment


class _Pending:
    __slots__ = ("event", "response")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.response: Optional[dict] = None


class ObsClient:
    def __init__(self, on_event: Callable[[str, dict], None] | None = None,
                 on_disconnect: Callable[[str], None] | None = None) -> None:
        self.on_event = on_event or (lambda t, d: None)
        self.on_disconnect = on_disconnect or (lambda reason: None)
        self._ws: Optional[websocket.WebSocket] = None
        self._send_lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._pending_lock = threading.Lock()
        self._ids = itertools.count(1)
        self._reader: Optional[threading.Thread] = None
        self._connected = threading.Event()
        self.negotiated_rpc = 0
        self.server_version = ""

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    # ------------------------------------------------------------------ connect
    def connect(self, host: str, port: int, password: str = "", timeout: float = 5.0) -> None:
        self.close()
        ws = websocket.WebSocket()
        url = f"ws://{host}:{port}"
        try:
            ws.connect(url, timeout=timeout)
            ws.settimeout(timeout)
            hello = self._recv_json(ws)
            if hello.get("op") != P.OP_HELLO:
                raise ObsError("unexpected handshake message")
            d = hello.get("d", {})
            self.server_version = d.get("obsWebSocketVersion", "")
            identify: dict[str, Any] = {"rpcVersion": P.RPC_VERSION,
                                        "eventSubscriptions": P.EVENT_SUBSCRIPTIONS}
            auth = d.get("authentication")
            if auth:
                if not password:
                    raise ObsAuthError("OBS requires a WebSocket password")
                identify["authentication"] = P.auth_string(password, auth["salt"], auth["challenge"])
            self._send_raw(ws, {"op": P.OP_IDENTIFY, "d": identify})
            msg = self._recv_json(ws)
            if msg.get("op") != P.OP_IDENTIFIED:
                raise ObsError("identification failed")
            self.negotiated_rpc = msg.get("d", {}).get("negotiatedRpcVersion", 0)
        except _Closed as c:
            ws.close()
            if c.code == P.CLOSE_AUTHENTICATION_FAILED:
                raise ObsAuthError("authentication failed (wrong password)") from None
            raise ObsError(f"connection closed by OBS ({c.code}: {c.reason})") from None
        except (OSError, websocket.WebSocketException) as e:
            ws.close()
            raise ObsError(f"cannot connect to {url}: {e}") from None
        except Exception:
            ws.close()
            raise
        ws.settimeout(None)
        self._ws = ws
        self._connected.set()
        self._reader = threading.Thread(target=self._read_loop, args=(ws,), name="obs-reader",
                                        daemon=True)
        self._reader.start()

    def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            self._connected.clear()
            try:
                ws.close(timeout=1)
            except Exception:
                pass
            self._fail_pending("connection closed")
        if self._reader is not None and self._reader is not threading.current_thread():
            self._reader.join(timeout=2)
        self._reader = None

    # ------------------------------------------------------------------ requests
    def request(self, request_type: str, data: dict | None = None, timeout: float = 3.0) -> dict:
        if request_type not in P.ALLOWED_REQUESTS:
            raise ObsError(f"request {request_type!r} is not permitted by DJ Director")
        ws = self._ws
        if ws is None or not self.connected:
            raise ObsError("not connected to OBS")
        rid = f"djd-{next(self._ids)}"
        pend = _Pending()
        with self._pending_lock:
            self._pending[rid] = pend
        msg = {"op": P.OP_REQUEST, "d": {"requestType": request_type, "requestId": rid}}
        if data:
            msg["d"]["requestData"] = data
        try:
            self._send_raw(ws, msg)
            if not pend.event.wait(timeout):
                raise TimeoutError(f"{request_type} timed out after {timeout:.1f}s")
        finally:
            with self._pending_lock:
                self._pending.pop(rid, None)
        resp = pend.response
        if resp is None:
            raise ObsError("connection lost during request")
        status = resp.get("requestStatus", {})
        if not status.get("result", False):
            raise ObsRequestError(request_type, int(status.get("code", 0)), status.get("comment", ""))
        return resp.get("responseData", {}) or {}

    # ------------------------------------------------------------------ internals
    def _send_raw(self, ws: websocket.WebSocket, msg: dict) -> None:
        with self._send_lock:
            ws.send(json.dumps(msg))

    @staticmethod
    def _recv_json(ws: websocket.WebSocket) -> dict:
        opcode, data = ws.recv_data()
        if opcode == websocket.ABNF.OPCODE_CLOSE:
            code = int.from_bytes(data[:2], "big") if len(data) >= 2 else 1005
            raise _Closed(code, data[2:].decode("utf-8", "replace"))
        return json.loads(data)

    def _read_loop(self, ws: websocket.WebSocket) -> None:
        reason = "connection lost"
        try:
            while True:
                msg = self._recv_json(ws)
                op = msg.get("op")
                d = msg.get("d", {})
                if op == P.OP_REQUEST_RESPONSE:
                    with self._pending_lock:
                        pend = self._pending.get(d.get("requestId", ""))
                    if pend is not None:
                        pend.response = d
                        pend.event.set()
                elif op == P.OP_EVENT:
                    try:
                        self.on_event(d.get("eventType", ""), d.get("eventData", {}) or {})
                    except Exception:
                        log.exception("OBS event handler failed")
        except _Closed as c:
            reason = f"closed by OBS ({c.code})"
        except Exception as e:
            if self._ws is ws:
                reason = f"connection lost: {e}"
            else:
                reason = "closed"
        finally:
            was_current = self._ws is ws
            self._connected.clear() if was_current else None
            self._fail_pending(reason)
            if was_current:
                self._ws = None
                try:
                    ws.close()
                except Exception:
                    pass
                self.on_disconnect(reason)

    def _fail_pending(self, reason: str) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
        for p in pending:
            p.event.set()


class _Closed(Exception):
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(code, reason)
        self.code = code
        self.reason = reason
