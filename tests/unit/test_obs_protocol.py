import base64
import hashlib

import pytest

from dj_director.obs import protocol as P
from dj_director.obs.client import ObsClient, ObsError


def test_auth_string_matches_spec_formula():
    pw, salt, challenge = "supersecretpassword", "lM1GncleQOaCu9lT1yeUZhFYnqhsLLP1G5lAGo3ixaI=", \
        "+IxH4CnCiqpX1rM9scsNynZzbOe4KhDeYcTNS3PDaeY="
    secret = base64.b64encode(hashlib.sha256((pw + salt).encode()).digest()).decode()
    expected = base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()
    assert P.auth_string(pw, salt, challenge) == expected


def test_only_allowlisted_requests():
    c = ObsClient()
    for forbidden in ("RemoveScene", "CreateScene", "StartRecord", "StopStream", "SetInputVolume",
                      "SetCurrentProfile", "SetSourceFilterEnabled", "SetCurrentPreviewScene"):
        with pytest.raises(ObsError, match="not permitted"):
            c.request(forbidden)
    assert "SetCurrentProgramScene" in P.ALLOWED_REQUESTS
