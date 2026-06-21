"""LiveKit room pre-creation with explicit agent dispatch.

Why this exists
---------------
Without server-side room pre-creation, LiveKit auto-creates the room on the
first publisher join. Auto-created rooms have no ``RoomAgentDispatch`` config,
so the LiveKit Agents dispatcher never invokes our worker for that room —
the user joins a silent room and the call stalls. They also have no room
metadata, which the worker's ``entrypoint`` reads to pick the persona, run
context, and user identity.

This helper calls ``RoomService.CreateRoom`` with:

* ``metadata`` — a compact JSON blob containing ``agent_id``, ``run_id``,
  ``session_id``, and ``user_id``. The worker reads this on dispatch
  (``ctx.room.metadata``) and uses it to load the persona + run context.
* ``agents=[RoomAgentDispatch(agent_name=...)]`` — tells the dispatcher to
  invoke the registered worker with this agent_name on the first publisher
  join. Without this field, an explicit-dispatch worker (one registered with
  ``WorkerOptions(agent_name=...)``) never gets a job for the room.

The call is idempotent: re-creating an existing room raises
``TwirpError`` with code ``ALREADY_EXISTS``, which we treat as success.
Any other Twirp error is re-raised.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from livekit.api import (
    CreateRoomRequest,
    LiveKitAPI,
    RoomAgentDispatch,
    TwirpError,
)

logger = logging.getLogger("tradingagents.web.voice.livekit_admin")


async def ensure_room(
    *,
    url: str,
    api_key: str,
    api_secret: str,
    room: str,
    metadata: dict,
    agent_name: str,
    max_participants: int = 2,
    empty_timeout: int = 60,
) -> None:
    """Idempotently create a LiveKit room with agent dispatch + metadata.

    Args:
        url: LiveKit Cloud / self-hosted URL (e.g. ``wss://x.livekit.cloud``).
            The LiveKit server SDK accepts ``wss://`` and translates internally
            to the HTTPS REST endpoint.
        api_key, api_secret: LiveKit project credentials.
        room: Room name. Must match the room the client will join.
        metadata: Arbitrary JSON-serializable dict. Stored as a string on the
            room and read by the worker via ``ctx.room.metadata``.
        agent_name: The worker's registered ``WorkerOptions.agent_name``. The
            dispatcher uses this to route the room to the right worker pool.
        max_participants: Hard cap on participants. Voice = 1 user + 1 agent
            ≤ 2.
        empty_timeout: Seconds the room stays alive with no publishers before
            LiveKit auto-deletes it.

    Raises:
        TwirpError: For any LiveKit server error other than ``ALREADY_EXISTS``.
    """
    metadata_json = json.dumps(metadata, separators=(",", ":"))
    request = CreateRoomRequest(
        name=room,
        metadata=metadata_json,
        empty_timeout=empty_timeout,
        max_participants=max_participants,
        agents=[RoomAgentDispatch(agent_name=agent_name)],
    )

    api: Any = LiveKitAPI(url, api_key, api_secret)
    try:
        await api.room.create_room(request)
        logger.info(
            "voice room created: room=%s agent=%s (dispatch wired)",
            room,
            agent_name,
        )
    except TwirpError as exc:
        code = getattr(exc, "code", "") or ""
        if str(code).lower() == "already_exists":
            logger.debug("voice room already exists, treating as success: %s", room)
            return
        raise
    finally:
        await api.aclose()
