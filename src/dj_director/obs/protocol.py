"""obs-websocket 5.x protocol constants and helpers.

Reference: https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md
Only the subset this application needs is implemented.
"""

from __future__ import annotations

import base64
import hashlib

RPC_VERSION = 1

OP_HELLO = 0
OP_IDENTIFY = 1
OP_IDENTIFIED = 2
OP_REIDENTIFY = 3
OP_EVENT = 5
OP_REQUEST = 6
OP_REQUEST_RESPONSE = 7

# EventSubscription bit flags
SUB_GENERAL = 1 << 0
SUB_CONFIG = 1 << 1
SUB_SCENES = 1 << 2
SUB_UI = 1 << 10
EVENT_SUBSCRIPTIONS = SUB_GENERAL | SUB_CONFIG | SUB_SCENES | SUB_UI

# WebSocketCloseCode values we treat specially
CLOSE_AUTHENTICATION_FAILED = 4009
CLOSE_UNSUPPORTED_RPC_VERSION = 4010

# The *only* requests this application ever sends. Anything else is refused by
# the client, which guarantees we never create/delete scenes, touch filters,
# audio, profiles, or start/stop outputs. GetSourceScreenshot (0.2, scene
# awareness) is read-only: it renders a small still of a scene and changes nothing.
ALLOWED_REQUESTS = frozenset({
    "GetVersion",
    "GetSceneList",
    "GetStudioModeEnabled",
    "GetSceneCollectionList",
    "SetCurrentProgramScene",
    "GetSourceScreenshot",
})


def auth_string(password: str, salt: str, challenge: str) -> str:
    """base64(sha256(base64(sha256(password + salt)) + challenge))"""
    secret = base64.b64encode(hashlib.sha256((password + salt).encode("utf-8")).digest()).decode("ascii")
    return base64.b64encode(hashlib.sha256((secret + challenge).encode("utf-8")).digest()).decode("ascii")
