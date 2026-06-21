"""LiveKit access-token minting.

The web/iOS/RN client connects to LiveKit Cloud (or self-hosted SFU) using a
short-lived signed JWT we mint server-side. The JWT carries:

* ``identity`` — stable per-user id (so reconnects map to the same participant).
* ``video`` grant — ``roomJoin`` + ``room`` (the only allowed room).
* ``metadata`` — arbitrary JSON we expose to the room; we use it to wire the
  voice agent worker (agent_id + run_id + session_id + user context).

LiveKit JWT spec: https://docs.livekit.io/realtime/concepts/authentication/

We implement the minimal subset rather than depending on ``livekit-server-sdk``
so the FastAPI process can mint tokens without pulling in the full SDK. The
algorithm is HS256 with the LiveKit API secret; PyJWT (already a project dep
via ``pyjwt[crypto]``) handles the signing.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("tradingagents.web.voice.jwt")

try:
    import jwt as _pyjwt  # PyJWT, already in web/requirements.txt
except ImportError:  # pragma: no cover — caught at runtime by router 503
    _pyjwt = None  # type: ignore[assignment]


@dataclass(frozen=True)
class LiveKitGrant:
    """Subset of the LiveKit ``VideoGrant`` we actually need."""

    room: str
    can_publish: bool = True
    can_subscribe: bool = True
    can_publish_data: bool = True
    room_join: bool = True
    # The room is created on first publisher join; we don't preemptively
    # create empty rooms (the worker hangs the agent on its own). Set
    # room_create=True if you ever need pre-allocation for room state.
    room_create: bool = True

    def to_claim(self) -> dict:
        return {
            "room": self.room,
            "roomJoin": self.room_join,
            "roomCreate": self.room_create,
            "canPublish": self.can_publish,
            "canSubscribe": self.can_subscribe,
            "canPublishData": self.can_publish_data,
        }


def mint_room_token(
    *,
    api_key: str,
    api_secret: str,
    identity: str,
    room: str,
    name: Optional[str] = None,
    ttl_seconds: int = 1800,
    metadata: Optional[dict] = None,
) -> str:
    """Sign a LiveKit access JWT for one client.

    Raises:
        RuntimeError: PyJWT is not installed (the rest of the app guards this
            with a 503).
        ValueError: ``api_key`` or ``api_secret`` is empty.
    """
    if _pyjwt is None:
        raise RuntimeError(
            "PyJWT is required to mint LiveKit tokens. "
            "Install with: pip install 'pyjwt[crypto]'"
        )
    if not api_key or not api_secret:
        raise ValueError("LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set")

    now = int(time.time())
    claims = {
        "iss": api_key,
        "sub": identity,
        "iat": now,
        "nbf": now,
        "exp": now + max(60, int(ttl_seconds)),
        "video": LiveKitGrant(room=room).to_claim(),
    }
    if name:
        claims["name"] = name
    if metadata is not None:
        # LiveKit expects metadata as a JSON-encoded string, not an object.
        claims["metadata"] = json.dumps(metadata, separators=(",", ":"))
    return _pyjwt.encode(claims, api_secret, algorithm="HS256")
