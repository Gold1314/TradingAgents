"""The additive ``/api/v2/*`` mobile router.

Everything here is registered only when ``MOBILE_API_ENABLED`` is true (see
:func:`web.mobile.include_mobile_api`). The legacy ``/api/*`` endpoints in
``web/server.py`` are never touched, so the web app is unchanged when the flag
is off.

Endpoints:

* ``POST /api/v2/runs`` — authenticated, per-user rate-limited wrapper around
  run creation. Delegates to the *existing* run machinery in ``web/server.py``
  (``manager`` / ``_run_pipeline`` / ``_planned_nodes``) — the heavy pipeline is
  reused verbatim; v2 only adds auth + quota in front of it. Read the resulting
  run via the existing ``GET /api/runs/{id}`` and SSE endpoints.
* ``POST /api/v2/runs/{run_id}/orders`` — per-user-safe order submission. Places
  the run's proposed order (with optional ticket edits) through *this user's*
  broker from the per-user registry — never the process-wide ``get_broker()``
  singleton. Re-clamps size, forces the ticker from the run, and honours the
  server's dry-run/live config (see ``web/mobile/orders.py``).
* ``GET  /api/v2/robinhood/authorize`` — start server-mediated OAuth (per user).
* ``GET  /api/v2/robinhood/callback``  — Robinhood's redirect target; completes
  the exchange server-side, then 302s to the app deep link.
* ``GET  /api/v2/robinhood/status``    — per-user connection status (polling).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from types import ModuleType
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from web.mobile.auth import User, current_user
from web.mobile.broker_registry import registry
from web.mobile.oauth_flow import sessions
from web.mobile.orders import MobileOrderRequest, place_user_order
from web.mobile.rate_limit import RateLimiter
from web.mobile.settings import load_settings

logger = logging.getLogger("tradingagents.web.mobile.router")


class MobileRunRequest(BaseModel):
    """Mirror of ``web.server.RunRequest`` so the v2 body is decoupled from the
    legacy module's import graph. Converted to the server model before use."""

    ticker: str
    trade_date: str
    analysts: List[str] = ["market", "social", "news", "fundamentals"]
    asset_type: str = "stock"
    provider: Optional[str] = None
    deep_model: Optional[str] = None
    quick_model: Optional[str] = None
    max_debate_rounds: Optional[int] = None
    max_risk_rounds: Optional[int] = None
    force: bool = False


def build_mobile_router(server: ModuleType) -> APIRouter:
    """Build the v2 router. ``server`` is the (fully-initialised) web.server
    module, passed in by :func:`web.mobile.include_mobile_api` to avoid a
    circular import at module load."""
    router = APIRouter(prefix="/api/v2", tags=["mobile-v2"])

    _settings = load_settings()
    limiter = RateLimiter(per_hour=_settings.runs_per_hour, per_day=_settings.runs_per_day)
    order_limiter = RateLimiter(
        per_hour=_settings.orders_per_hour, per_day=_settings.orders_per_day
    )
    # Exposed for tests/admin to reset counters without reaching into closures.
    router.state_rate_limiter = limiter  # type: ignore[attr-defined]
    router.state_order_rate_limiter = order_limiter  # type: ignore[attr-defined]

    # ── runs (auth + per-user rate limit) ────────────────────────────────
    @router.post("/runs")
    async def create_run_v2(
        body: MobileRunRequest, request: Request, user: User = Depends(current_user)
    ) -> dict:
        if not body.ticker.strip():
            raise HTTPException(status_code=400, detail="ticker is required")
        if not body.analysts:
            raise HTTPException(status_code=400, detail="select at least one analyst")

        retry_after = limiter.check_and_consume(user.user_id)
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail="run quota exceeded; try again later",
                headers={"Retry-After": str(retry_after)},
            )

        req = server.RunRequest(**body.model_dump())
        loop = asyncio.get_running_loop()
        nodes = server._planned_nodes(req.analysts)
        run = server.manager.create(loop, nodes)
        # Record the owner (for per-user order authorization) and the ticker (so
        # the v2 order endpoint can force it server-side, independent of whether
        # the pipeline stashed a proposed order). ``store_event`` ignores extra
        # keys, so this is safe to carry through analytics.
        run.analytics = {"user_id": user.user_id, "ticker": req.ticker}
        server.manager.emit(run, {"type": "nodes", "nodes": nodes})

        thread = threading.Thread(
            target=server._run_pipeline, args=(run, req), daemon=True
        )
        thread.start()
        return {"cached": False, "run_id": run.run_id, "nodes": nodes}

    # ── per-user-safe order submission (LIVE trading, §5.3) ──────────────
    @router.post("/runs/{run_id}/orders")
    async def place_order_v2(
        run_id: str, body: MobileOrderRequest, user: User = Depends(current_user)
    ) -> dict:
        retry_after = order_limiter.check_and_consume(user.user_id)
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail="order quota exceeded; try again later",
                headers={"Retry-After": str(retry_after)},
            )
        settings = load_settings()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: place_user_order(server, registry, settings, user, run_id, body),
        )

    # ── Robinhood server-mediated OAuth (per user) ───────────────────────
    @router.get("/robinhood/authorize")
    async def robinhood_authorize_v2(user: User = Depends(current_user)) -> dict:
        settings = load_settings()
        if not settings.callback_url:
            raise HTTPException(
                status_code=503,
                detail="MOBILE_PUBLIC_BASE_URL is not configured for mobile OAuth.",
            )
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None, lambda: sessions.start_authorize(user.user_id, settings)
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return result

    @router.get("/robinhood/callback")
    async def robinhood_callback_v2(
        code: Optional[str] = None,
        state: Optional[str] = None,
        error: Optional[str] = None,
    ) -> RedirectResponse:
        settings = load_settings()
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: sessions.complete_callback(code, state, error)
        )
        status = "ok" if result.get("connected") else "error"
        sep = "&" if "?" in settings.app_redirect else "?"
        target = f"{settings.app_redirect}{sep}status={status}"
        if status == "error" and result.get("error"):
            target += f"&reason={quote(str(result['error']))}"
        return RedirectResponse(url=target, status_code=302)

    @router.get("/robinhood/status")
    async def robinhood_status_v2(user: User = Depends(current_user)) -> dict:
        settings = load_settings()
        loop = asyncio.get_running_loop()
        broker = registry.get(user.user_id, settings)
        return await loop.run_in_executor(None, broker.status)

    return router
