"""Per-user-safe order submission for the mobile (v2) API.

The legacy ``POST /api/robinhood/orders/{run_id}`` in ``web/server.py`` places
orders through the process-wide ``get_broker()`` singleton, which is fine for a
single-user desktop deployment but **not multi-user safe**: every user would
share one connection and one set of OAuth tokens. This module reproduces the
same "place the proposed order" behaviour, but scoped to the authenticated
user's own broker (from the per-user
:class:`~web.mobile.broker_registry.BrokerRegistry`) and the user's own run.

Server-side safety is enforced here independently of any client-side biometric
gate (``docs/ios-app-plan.md`` §5.3) — the biometric prompt is a UX guard, not a
security boundary:

* the order **ticker is forced from the run**, never taken from the client;
* notional/quantity are **re-clamped** to the account's limits inside the
  executor (:func:`tradingagents.brokers.executor.clamp_intent`);
* **dry-run vs live** is decided by the *server's* per-user broker config
  (``can_place_real_orders`` / ``trade_mode`` / ``dry_run``), never the client;
* the order is **rejected unless the user's broker is connected**;
* placement is **idempotent** per run (a double-submit returns the first
  result rather than firing a second order).

No order-placement logic is re-implemented here — the existing
:class:`~tradingagents.brokers.executor.AutoTradeExecutor` and
:class:`~tradingagents.brokers.intents.OrderIntent` are reused verbatim.
"""

from __future__ import annotations

import logging
from types import ModuleType
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel

from tradingagents.brokers.executor import AutoTradeExecutor
from tradingagents.brokers.intents import ExecutionResult, OrderIntent
from web.mobile.auth import User
from web.mobile.broker_registry import BrokerRegistry
from web.mobile.settings import MobileSettings

logger = logging.getLogger("tradingagents.web.mobile.orders")


class MobileOrderRequest(BaseModel):
    """Edits to the proposed order ticket (mirror of ``web.server.PlaceOrderRequest``).

    All fields are optional and overlay the run's agent-proposed order; when the
    run has no stashed proposal the client must supply ``action`` plus a size.
    The ticker is intentionally **not** accepted — an order can only ever target
    the run's own ticker, which is forced server-side.
    """

    action: Optional[str] = None
    quantity: Optional[float] = None
    notional: Optional[float] = None
    order_type: Optional[str] = None


def _trade_event(result: ExecutionResult, cfg) -> dict:
    """Match the legacy ``web.server._trade_event`` payload shape exactly so the
    iOS client can repoint without changing its response parsing."""
    return {
        "type": "trade",
        "trade_mode": cfg.trade_mode,
        "can_place_real_orders": cfg.can_place_real_orders,
        "dry_run": cfg.dry_run,
        **result.to_dict(),
    }


def _run_ticker(run) -> Optional[str]:
    """The ticker an order for ``run`` must target — forced from server state.

    Prefers the ticker the pipeline stashed alongside a proposed order
    (``order_ticker``); falls back to the value the v2 run endpoint records in
    ``analytics`` at creation time. The client's value is never consulted.
    """
    ticker = getattr(run, "order_ticker", None)
    if not ticker:
        analytics = getattr(run, "analytics", None) or {}
        ticker = analytics.get("ticker")
    return str(ticker).strip().upper() if ticker else None


def place_user_order(
    server: ModuleType,
    registry: BrokerRegistry,
    settings: MobileSettings,
    user: User,
    run_id: str,
    body: MobileOrderRequest,
) -> dict:
    """Place ``user``'s order for ``run_id`` via *their own* broker.

    Returns the trade payload (status ``placed`` / ``dry_run`` / ``skipped`` /
    ``error``) on success, mirroring the legacy contract. Raises
    :class:`fastapi.HTTPException` for auth/ownership/state/validation failures.

    Blocking (the broker calls out over MCP), so callers run it in a thread-pool
    executor off the event loop.
    """
    run = server.manager.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown or expired run")

    # Ownership: a user may only place orders against their own v2 run. The v2
    # run endpoint records the owner's user_id in ``analytics``. Return 404 (not
    # 403) so we don't reveal the existence of another user's run.
    owner = (getattr(run, "analytics", None) or {}).get("user_id")
    if owner != user.user_id:
        raise HTTPException(status_code=404, detail="unknown or expired run")

    ticker = _run_ticker(run)
    if not ticker:
        raise HTTPException(status_code=409, detail="run has no associated ticker")

    broker = registry.get(user.user_id, settings)
    cfg = broker.cfg
    if not cfg.executes_orders:  # trade_mode == "off" (or integration disabled)
        raise HTTPException(status_code=409, detail="Robinhood execution is off.")

    # Idempotency: reuse the run's order lock/result so a double-submit returns
    # the first placement instead of firing twice (same guarantee the legacy
    # path gives via these same fields).
    with run.order_lock:
        if getattr(run, "order_placed", False) and getattr(run, "order_result", None):
            return run.order_result

        # Start from the agent's proposal (if the pipeline stashed one) and
        # overlay only the client's ticket edits. The ticker is forced to the
        # run's own ticker — a client-supplied symbol is never trusted.
        intent_dict = dict(getattr(run, "pending_order", None) or {})
        intent_dict["ticker"] = ticker
        if body.action:
            intent_dict["action"] = body.action
        if body.order_type:
            intent_dict["order_type"] = body.order_type
        if body.quantity is not None:
            intent_dict["quantity"] = body.quantity
        if body.notional is not None:
            intent_dict["notional"] = body.notional

        intent = OrderIntent.from_dict(intent_dict)
        if not intent.is_actionable:
            raise HTTPException(
                status_code=400,
                detail="no actionable order: provide action=buy|sell with a size",
            )
        # One sizing field per action: buys use notional, sells use quantity.
        if intent.action == "buy":
            intent.quantity = None
            if intent.notional is None or intent.notional <= 0:
                raise HTTPException(
                    status_code=400, detail="buy order requires a positive notional"
                )
        elif intent.action == "sell":
            intent.notional = None
            if intent.quantity is None or intent.quantity <= 0:
                raise HTTPException(
                    status_code=400, detail="sell order requires a positive quantity"
                )

        # Connect headlessly from this user's saved tokens if needed; never reach
        # for the process-wide singleton.
        if not broker.is_connected and broker.has_saved_credentials():
            try:
                broker.connect()
            except Exception as exc:  # noqa: BLE001 — report as not-connected
                logger.warning("per-user broker connect failed: %s", exc)
        if not broker.is_connected:
            raise HTTPException(status_code=409, detail="Robinhood not connected.")

        # The executor re-clamps notional/quantity against a fresh snapshot and
        # honours dry_run vs live from the server's config — the authoritative
        # safety guard regardless of what the client sent.
        snapshot = broker.get_account_snapshot()
        result = AutoTradeExecutor(broker, cfg).execute_intent(intent, snapshot)
        payload = _trade_event(result, cfg)
        if result.status == "placed":
            run.order_placed = True
            run.order_result = payload
        return payload
