"""Additive mobile (iOS) API for StockAgents — default OFF.

This package adds a versioned ``/api/v2/*`` surface for the native iOS app
*alongside* the existing web API, without modifying any endpoint the web client
depends on. The entire surface is gated behind the ``MOBILE_API_ENABLED``
environment flag (default false), so with default configuration the web app's
behaviour is unchanged.

Wiring: ``web/server.py`` calls :func:`include_mobile_api` exactly once at the
very bottom of the module. When the flag is off, this is a no-op and nothing
from this package is imported beyond this file.

Modules
-------
* ``settings``        — env-gated configuration (the feature flags).
* ``auth``            — ``current_user`` identity dependency (dev / supabase).
* ``rate_limit``      — per-user fixed-window quota for the run endpoint.
* ``broker_registry`` — per-user broker + per-user token storage isolation.
* ``oauth_flow``      — server-mediated Robinhood OAuth connect sessions.
* ``router``          — the ``/api/v2/*`` FastAPI router.
"""

from __future__ import annotations

import logging

from web.mobile.settings import mobile_api_enabled

logger = logging.getLogger("tradingagents.web.mobile")

__all__ = ["include_mobile_api", "mobile_api_enabled"]


def include_mobile_api(app) -> bool:
    """Register the ``/api/v2/*`` router on ``app`` iff the feature is enabled.

    Returns True if the router was registered. Safe to call unconditionally —
    when ``MOBILE_API_ENABLED`` is false it does nothing and imports nothing
    further from this package.
    """
    if not mobile_api_enabled():
        return False

    # Deferred imports: ``web.mobile.router`` reaches into ``web.server`` (the
    # module that calls us at its very bottom). Importing here — after
    # ``web.server`` is fully defined — avoids a circular import at module load.
    # Documented exception to the no-inline-imports rule (strict circular dep).
    from web import server  # noqa: PLC0415
    from web.mobile.router import build_mobile_router  # noqa: PLC0415

    app.include_router(build_mobile_router(server))
    logger.info("Mobile API (/api/v2) enabled.")
    return True
