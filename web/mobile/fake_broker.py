"""In-memory fake broker for testing the trading UI without live Robinhood.

The v2 trading surface (``/api/v2/robinhood/status`` and
``POST /api/v2/runs/{run_id}/orders``) is only reachable through a *connected*
broker: with no saved OAuth tokens the real :class:`RobinhoodBroker` reports
``connected: false`` and the order endpoint rejects with 409. That makes it
impossible to exercise the connect-status badge, the account snapshot, or the
order-ticket + biometric LIVE gate in the iOS Simulator (or in tests) unless a
real Robinhood agentic account is connected.

This module provides a drop-in :class:`FakeBroker` that mirrors the subset of
the :class:`~tradingagents.brokers.robinhood_mcp.RobinhoodBroker` surface the v2
endpoints touch, but is entirely in-memory:

* it reports **connected** with deterministic mock account data, so the status
  badge and account snapshot render as "connected";
* ``review_order``/``place_order`` never hit the network — and, crucially, even
  with ``dry_run=false`` (LIVE), :meth:`place_order` returns a **fake** order id
  rather than sending a real order, so the full LIVE confirm + Face ID gate can
  be rehearsed end-to-end without any real money moving.

It is wired in **only** when ``MOBILE_FAKE_BROKER`` is set (default OFF, see
:mod:`web.mobile.settings`) via :func:`web.mobile.broker_registry.build_user_broker`.
The server-side safety pipeline (ticker forced from the run, re-clamping,
idempotency, dry-run-vs-live decided by config) in ``web/mobile/orders.py`` is
reused verbatim — this only stands in for the live MCP transport. It is a
test/dev aid and must never be enabled in production.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from tradingagents.brokers.config import RobinhoodConfig
from tradingagents.brokers.intents import AccountSnapshot, OrderIntent

logger = logging.getLogger("tradingagents.web.mobile.fake_broker")

# Deterministic mock account the fake broker reports. Big enough buying power
# that a default-size buy is never clamped to zero, and a held NVDA position so
# a sell has something to act on.
FAKE_ACCOUNT_NUMBER = "FAKE000001"
FAKE_BUYING_POWER = 10_000.0
FAKE_PORTFOLIO_VALUE = 25_000.0
FAKE_POSITIONS: List[Dict[str, Any]] = [
    {"symbol": "NVDA", "quantity": "12", "average_buy_price": "100.00"},
]
# Tool names mirror Robinhood's real catalogue (config.py tool_overrides) so the
# status payload looks realistic to the client.
FAKE_TOOLS = [
    "get_accounts",
    "get_equity_positions",
    "get_portfolio",
    "place_equity_order",
    "review_equity_order",
]


class _FakeTokenStorage:
    """Minimal stand-in so ``broker.storage`` access never errors. The fake
    broker is always "connected", so saved-credential checks return True."""

    def has_tokens(self) -> bool:
        return True


class FakeBroker:
    """In-memory stand-in for :class:`RobinhoodBroker` (no network, ever)."""

    SERVER_KEY = "robinhood-trading-fake"

    def __init__(self, cfg: RobinhoodConfig):
        self.cfg = cfg
        self.storage = _FakeTokenStorage()
        self._connected = True
        self._order_seq = 0
        # Recorded for assertions in tests (which intents were placed/reviewed).
        self.placed: List[OrderIntent] = []
        self.reviewed: List[OrderIntent] = []

    # ── connection ───────────────────────────────────────────────────────
    @property
    def is_connected(self) -> bool:
        return self._connected

    def has_saved_credentials(self) -> bool:
        return True

    def connect(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """No-op connect — the fake broker is always already connected."""
        self._connected = True
        return self.status()

    def disconnect(self) -> None:
        self._connected = False

    def status(self) -> Dict[str, Any]:
        """Match :meth:`RobinhoodBroker.status` so the iOS client parses it
        unchanged, plus an additive ``account`` block with the mock snapshot."""
        snapshot = self.get_account_snapshot()
        return {
            **self.cfg.public_status(),
            "available": True,
            "connected": self._connected,
            "has_saved_credentials": True,
            "tools": list(FAKE_TOOLS),
            "error": None,
            # Additive; unknown keys are ignored by clients that don't read it.
            "account": {
                "connected": self._connected,
                "buying_power": snapshot.buying_power,
                "portfolio_value": snapshot.portfolio_value,
                "currency": snapshot.currency,
                "account_number": snapshot.account_number,
                "positions": snapshot.positions,
            },
        }

    # ── account reads ────────────────────────────────────────────────────
    def get_account_number(self) -> Optional[str]:
        return FAKE_ACCOUNT_NUMBER

    def get_account_snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(
            buying_power=FAKE_BUYING_POWER,
            portfolio_value=FAKE_PORTFOLIO_VALUE,
            currency="USD",
            account_number=FAKE_ACCOUNT_NUMBER,
            positions=[dict(p) for p in FAKE_POSITIONS],
        )

    def get_holdings(self) -> List[Dict[str, Any]]:
        return [dict(p) for p in FAKE_POSITIONS]

    # ── order review / placement (never networked) ───────────────────────
    def review_order(self, intent: OrderIntent) -> Dict[str, Any]:
        """Return a plausible review payload for the dry-run path."""
        self.reviewed.append(intent)
        return {"quote_data": {"last_trade_price": "123.45"}, "order_checks": []}

    def place_order(self, intent: OrderIntent) -> Tuple[str, Dict[str, Any]]:
        """Simulate a placement. Even in LIVE mode this returns a *fake* order
        id and never contacts Robinhood — the point is to rehearse the full
        confirm + biometric flow safely."""
        self._order_seq += 1
        order_id = f"fake-order-{self._order_seq}"
        self.placed.append(intent)
        logger.info("FakeBroker simulated order %s: %s", order_id, intent.to_dict())
        return order_id, {"id": order_id, "state": "confirmed", "fake": True}


def build_fake_broker(cfg: RobinhoodConfig) -> FakeBroker:
    """Construct a :class:`FakeBroker` for ``cfg`` (factory for the registry)."""
    return FakeBroker(cfg)
