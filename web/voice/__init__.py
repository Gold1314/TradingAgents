"""Voice session API — additive, default OFF.

Mounts ``/api/voice/*`` on the main FastAPI app *only* when
:func:`tradingagents.voice.settings.voice_enabled` returns true. With the
feature off, this package imports nothing from LiveKit and the web app is
unchanged.

Wiring lives in :func:`web.voice.include_voice_api`, called once from
``web/server.py`` at the very bottom (next to ``include_mobile_api``).
"""

from __future__ import annotations

import logging

from tradingagents.voice.settings import voice_enabled

logger = logging.getLogger("tradingagents.web.voice")

__all__ = ["include_voice_api", "voice_enabled"]


def include_voice_api(app) -> bool:
    """Register the ``/api/voice/*`` router on ``app`` iff voice is enabled.

    Safe to call unconditionally — when the feature is off, this does nothing
    and imports nothing further from the package. Returns True if the router
    was registered.
    """
    if not voice_enabled():
        return False

    # Deferred imports: the voice router reaches into ``web.server`` (the
    # module that calls us at its very bottom). Importing here — after
    # ``web.server`` is fully defined — avoids a circular import at module
    # load. Documented exception to the no-inline-imports rule (strict
    # circular dep). Mirrors the pattern in :mod:`web.mobile`.
    from web import server  # noqa: PLC0415
    from web.voice.router import build_voice_router  # noqa: PLC0415

    app.include_router(build_voice_router(server))
    logger.info("Voice API (/api/voice) enabled.")
    return True
